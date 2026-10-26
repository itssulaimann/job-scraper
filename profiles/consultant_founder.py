"""
Candidate profile: consultant / founder's-associate / venture track
(Germany-wide). llm_fit_scorer.py's sole fit signal now (the old regex signal
table has been removed).

Breadth is the qualification here, not a liability — the opposite framing
from the PM profile: client/stakeholder work, cross-functional scope, and
"many hats" generalism all count as strengths, not noise.

Tier 1 (best odds): Founder's Associate / Chief of Staff, Venture / EIR /
Investment Associate, Product & Technology Consultant, Founding PM.
Tier 2: Technical Account Manager / Implementation Consultant, greenfield-only
PM, technical BD / Partnerships, Project & Delivery Manager.
Tier 3: Product Owner at mid-size German companies, Growth / Marketing Ops.

Bump LLM_FIT_VERSION whenever this brief changes.
"""
from __future__ import annotations

LLM_FIT_VERSION = 1

CANDIDATE_BRIEF = """\
Consultant / Founder's Associate / venture-track candidate, Germany-wide
(not city-restricted).

Target roles, in descending order of fit:
- Tier 1: Founder's Associate, Chief of Staff, Entrepreneur/Founder in
  Residence, Venture/Investment Associate or Analyst, Product or Technology
  Consultant, Founding Product Manager.
- Tier 2: Technical Account Manager, Implementation Consultant, Solutions
  Consultant/Architect, Business Development / Partnerships Manager,
  Delivery / Project / Program Manager.
- Tier 3: Product Owner at a mid-size German company, Growth or Marketing
  Operations.

Breadth is the qualification, not a liability — this is the opposite of a
narrow specialist search. Score client-facing work, cross-functional scope,
stakeholder management, and "wears many hats" generalist roles as strengths.
Also value: 0-to-1 / greenfield work, defining or owning the product,
entrepreneurial ownership mindset, hands-on and technically fluent (reads API
specs, comfortable with SQL, technical background), scoping or closing deals,
ambiguity, autonomy, fast-paced early-stage environments (seed/pre-seed/Series
A, early hires), AI-native tooling, and English as the team's working language.

Avoid / score down: postings requiring 10+ years of experience, roles that
are deep niche specialism only (not a generalist mandate), heavy formal
credentialing (PhD, mandatory MBA, CPA/chartered accountant), roles requiring
a professional licence or security clearance, and mass-market operations work
(call center, shift work, warehouse floor).

Seniority: exec-level titles (Head of, VP, Chief-anything, C-suite, Director,
Geschäftsführer) are hard misses on career_alignment — EXCEPT Chief of Staff,
which is deliberately a Tier 1 target, not an exec title. Internship, working
student (Werkstudent), Praktikum, and apprenticeship (Ausbildung) postings are
also hard misses regardless of how well the rest of the posting reads.

Language: this profile does not have the candidate's specific language
proficiency on file. Do not assume a level — if the posting states a language
requirement for the role itself (not just the language the ad happens to be
written in), FLAG it in the language dimension rather than passing or failing
it outright, so a human reviews the actual requirement.
"""
