"""Lightweight per-IP rate limiting, body-size caps and security headers.

In-memory sliding-window limiter (the app runs a single uvicorn worker). Rules
are matched most-specific-first; a global per-IP cap sits over everything. The
scrape endpoint is the sensitive one — it triggers the server-side Claude QA and
outbound scraping — so it's gated by password AND tightly rate-limited here.

Wired in by server.py via `security.init(app)`.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

# (path prefix, max requests, window seconds) — first match wins.
RULES = [
    ("/api/scrape", int(os.getenv("RL_SCRAPE", "5")),  60),   # admin-only; stop hammering/brute-force
    ("/api/audit",  int(os.getenv("RL_AUDIT",  "20")), 60),   # CPU-heavy (rapidfuzz clustering)
    ("/api/track",  int(os.getenv("RL_TRACK",  "60")), 60),   # analytics beacon
    ("/api/",       int(os.getenv("RL_API",    "120")),60),   # everything else under /api
]
GLOBAL_MAX = int(os.getenv("RL_GLOBAL", "300"))               # per IP across all paths
GLOBAL_WINDOW = 60
MAX_BODY = int(os.getenv("MAX_BODY_BYTES", str(256 * 1024)))  # 256 KB

SEC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), browsing-topics=()",
}

_HITS: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
_req_count = 0


def _ip(request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _prune(now: float) -> None:
    """Drop IPs whose windows are all empty, to bound memory."""
    for ip in list(_HITS.keys()):
        buckets = _HITS[ip]
        for cls in list(buckets.keys()):
            dq = buckets[cls]
            while dq and dq[0] < now - GLOBAL_WINDOW:
                dq.popleft()
            if not dq:
                del buckets[cls]
        if not buckets:
            del _HITS[ip]


def init(app) -> None:
    class RateLimit(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            # static assets are cheap and cached — don't meter them
            if path.startswith("/static/"):
                resp = await call_next(request)
                for k, v in SEC_HEADERS.items():
                    resp.headers.setdefault(k, v)
                return resp

            # reject oversized bodies before reading them
            if request.method in ("POST", "PUT", "PATCH"):
                try:
                    if int(request.headers.get("content-length", 0)) > MAX_BODY:
                        return PlainTextResponse("Payload too large.", status_code=413)
                except ValueError:
                    pass

            ip = _ip(request)
            now = time.time()

            global _req_count
            _req_count += 1
            if _req_count % 2000 == 0:
                _prune(now)

            # specific rule (first match) + the global cap
            cls, limit, window = "_api_default", None, 60
            for prefix, lim, win in RULES:
                if path.startswith(prefix):
                    cls, limit, window = prefix, lim, win
                    break

            buckets = _HITS[ip]
            gdq = buckets["_global"]
            while gdq and gdq[0] < now - GLOBAL_WINDOW:
                gdq.popleft()
            if len(gdq) >= GLOBAL_MAX:
                return PlainTextResponse("Too many requests — slow down.", status_code=429,
                                         headers={"Retry-After": "30", **SEC_HEADERS})

            if limit is not None:
                dq = buckets[cls]
                while dq and dq[0] < now - window:
                    dq.popleft()
                if len(dq) >= limit:
                    retry = int(window - (now - dq[0])) + 1 if dq else window
                    return PlainTextResponse("Too many requests — slow down.", status_code=429,
                                             headers={"Retry-After": str(max(1, retry)), **SEC_HEADERS})
                dq.append(now)

            gdq.append(now)

            resp = await call_next(request)
            for k, v in SEC_HEADERS.items():
                resp.headers.setdefault(k, v)
            return resp

    app.add_middleware(RateLimit)
