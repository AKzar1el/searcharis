# Searcharis — Revenue Shot worker charter

**State:** Prepared instructions only. This document does not activate a
scheduled worker, grant credentials, or authorize deployment.

## Product direction

Evolve Searcharis from its original hackathon deployment-monitoring
demonstration into an installable, **local-first search regression CI**
product for web teams and SEO agencies. Preserve the existing evidence-gated
incident workflow; do not make the deployed services a prerequisite for
offline development.

## Every wake

1. Inventory the actual checkout, Git HEAD/branch, owned changes,
   pending PRs, CI, project journal and active-writer safety **once**.
   Resume meaningful unfinished work on its existing branch before selecting
   new work. Never reset or overwrite unrelated files.
2. Inspect the current source baseline and choose **one bounded**
   improvement with measurable user value. Prefer actual failures,
   deterministic fixtures, CLI usability, actionable output, packaging,
   and documentation over vanity features.
3. Work on a normal project branch (not a linked worktree). Make the
   smallest coherent change. Add regression tests for defects.
4. Run `uv run pytest tests/unit tests/integration -q` and
   `uv run ruff check src tests demo_target`. Use the default offline
   fixtures; live-provider checks are separate, explicitly authorized gates.
5. Record truthful test results, decisions and dead ends in the owned
   journal; commit and open or update one focused PR. Follow the existing
   independent review/merge workflow. Do not self-approve.
6. Close out with SHA, PR URL, tests, blockers and next recommendation.

## Guardrails

- **Never** mutate Google Cloud, Gemini, Firestore, GitHub webhook settings,
  production secrets, customer sites or production data in an ordinary wake.
- No paid services or API calls, no automatic deployment, no destructive
  filesystem operations, and no silent fallback from failed checks to success.
- The new CI analyzer processes supplied built artifacts only; never use its
  `url` value for an outbound fetch.
- No fabricated demand/SEO metrics or unsupported security guarantees.
- Do not modify the schedules, prompts, branches or journals belonging to
  the seven established Revenue Shot workers.
- On inaccessible workspace, missing dependencies, or failed checks: log
  an explicit blocker; do not disguise incomplete work as a completed PR.

## Highest-value backlog

- [x] Multi-page build-manifest verification with clear coverage reporting.
- [x] Opt-in reciprocal HTML hreflang return-link tests using local bilingual fixtures.
- [x] Opt-in cross-page canonical target/self-link consistency checks.
- [x] Opt-in local locale-cluster consistency diagnostics beyond reciprocal HTML links.
- [ ] GitHub Action packaging with pinned dependencies, minimal permissions.
- [ ] Artifact comparisons and stable JSON/SARIF reporting.
- [ ] Usability studies against static Astro and React/Vite build outputs.
