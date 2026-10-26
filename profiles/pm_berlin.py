"""
Candidate profile: Berlin PM search (llm_fit_scorer.py).

Natural-language brief used to prompt the LLM rubric scorer — the sole fit
signal now (the old regex signal table has been removed). Bump
LLM_FIT_VERSION whenever this brief changes; main.py rescores any rows whose
stored version differs.
"""
from __future__ import annotations

LLM_FIT_VERSION = 3   # v3: gaps/strengths must cite something the posting states,
                      # not silence on a soft preference (team size, SQL/API, remote policy)

CANDIDATE_BRIEF = """\
Product Manager targeting Berlin or Munich, remote-friendly within Germany.
Also open to adjacent Founder's Associate / Chief of Staff / Entrepreneur-in-
Residence roles at early-stage startups.

Ideal role: 0-to-1 or founding PM work — defining a product from scratch in a
greenfield, no-playbook, AI-native environment. Comfortable being hands-on and
technical: reads API specs, writes SQL, works closely with engineers,
prototypes directly, uses AI tools day to day. Thrives on ambiguity and
autonomy inside a small team (roughly 2-6 engineers) at a fast-paced startup.

Seniority target: mid-to-senior individual-contributor PM/PO. NOT looking for
staff/principal IC tracks or people-management/exec roles (Head of Product, VP
Product, CPO, Director of Product, or leading a team of PMs) — those are hard
misses on career_alignment regardless of how well the rest of the posting reads.

Avoid: mature/scaling products, enterprise-grade software, large user bases
(millions of users, hundreds of enterprise customers), heavy cross-org
stakeholder management, and postings that require 10+ years of experience.

Languages: English fluent/native. German B1 — can hold a working conversation,
but is not fluent, not native, and holds no C1+ certificate. Judge the
language dimension directly against this: a posting silent on language, or
asking for "German a plus"/conversational German, is a clean pass; a hard
requirement above B1 (fluent, C1+, native, verhandlungssicher) for the role
itself is a real gap, not a rounding error.
"""
