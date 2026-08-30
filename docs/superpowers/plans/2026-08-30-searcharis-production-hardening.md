# Searcharis Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the proven Searcharis GCP deployment against transient provider failures, poison-message retry storms, crash windows around GitHub mutations, unbounded Firestore growth/queries, and accidental judging-period shutdown without changing the product’s core architecture.

**Architecture:** Preserve ingress -> Pub/Sub -> private worker -> validator -> ADK/Gemini -> deterministic policy -> GitHub -> Firestore -> Cloud Tasks -> fresh verification. Add explicit transient-error semantics, leased/reconcilable side-effect claims, native Firestore timestamps/TTL, bounded queries, DLQ/backoff, explicit Cloud Run resource limits, and judging guardrails around the same flow.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, Google ADK 2.x, Gemini 3.7 Flash on Vertex AI, Cloud Run, Pub/Sub, Firestore, Cloud Tasks, Secret Manager, GitHub REST API, pytest, Ruff, Bash, GitHub Actions/WIF.

**Spec:** `docs/superpowers/specs/2026-08-30-searcharis-production-hardening-design.md`

## Global Constraints

- Preserve the existing workflow and deterministic verification-before-close invariant.
- Keep Gemini model ID `gemini-3.7-flash` and Vertex location `global`.
- Preserve the public ingress / private worker split and existing service accounts.
- No GCP service-account JSON keys; GitHub deployment remains WIF/OIDC only.
- No new queue/database/framework and no destructive Firestore migration.
- Pub/Sub retry: 10s minimum, 60s maximum; DLQ `searcharis-deployments-dead-letter`; max delivery attempts 8.
- Operational record retention target: 30 days; incidents have no TTL.
- Cloud Run ingress: min 0, max 3, concurrency 20, 1 CPU, 512Mi, 60s timeout, startup CPU boost.
- Cloud Run worker: min 0, max 2, concurrency 4, 1 CPU, 1Gi, 600s timeout, startup CPU boost.
- Keep live through October 1, 2026 11:45 PM PT.
- No merge unless full CI and the real five-duplicate GCP proof pass.

---

### Task 1: Classify Gemini failures and add bounded exponential backoff

**Files:**
- Modify: `src/searcharis/agent/diagnostician.py`
- Modify: `src/searcharis/services/orchestrator.py`
- Modify: `tests/unit/test_diagnostician.py`
- Modify: `tests/integration/test_orchestrator.py`

**Interfaces:**
- Produces: `DiagnosticianRetryableError`, `DiagnosticianInvalidOutputError`.
- `Diagnostician.__init__` gains injectable async `sleep_fn` and jitter source so tests never sleep.
- `Orchestrator` maps retryable model errors to `WorkflowState.FAILED_RETRYABLE`; invalid structured output remains `NEEDS_REVIEW`.

- [ ] **Step 1: Add failing unit tests for transient status classification**

Add tests that construct representative exceptions exposing `status_code`, `code`, or message text for 408, 429, 500, 502, 503, and 504 and assert they are classified retryable. Add a negative test for schema/JSON validation failures.

```python
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_model_statuses_are_retryable(status):
    exc = FakeProviderError(status)
    assert is_retryable_model_error(exc) is True


def test_invalid_json_is_not_retryable_provider_error():
    assert is_retryable_model_error(ValueError("invalid model output")) is False
```

- [ ] **Step 2: Run the targeted tests and verify failure**

Run: `uv run pytest tests/unit/test_diagnostician.py -q`

Expected: FAIL because the new exception types/classifier/backoff injection do not exist yet.

- [ ] **Step 3: Implement explicit diagnostician errors and backoff**

Implement:

```python
class DiagnosticianRetryableError(RuntimeError):
    pass

class DiagnosticianInvalidOutputError(ValueError):
    pass

TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
```

Add a small `is_retryable_model_error(exc: Exception) -> bool` helper that inspects stable exception attributes first and only then conservative message patterns such as `RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`, timeout/temporarily unavailable.

Retry at most 3 attempts. Before attempts 2 and 3 sleep with bounded exponential backoff plus injected jitter, e.g. base 0.5s, then 1.0s, capped under 2s for local retry because Pub/Sub handles longer backoff.

If all attempts fail transiently, raise `DiagnosticianRetryableError` chained from the last provider exception. If model execution succeeds but structured output is invalid on all attempts, raise `DiagnosticianInvalidOutputError`.

- [ ] **Step 4: Add orchestrator mapping tests**

Add a fake diagnostician that raises each error and assert:

```python
assert retryable_run.state == WorkflowState.FAILED_RETRYABLE
assert invalid_output_run.state == WorkflowState.NEEDS_REVIEW
```

- [ ] **Step 5: Run unit + orchestrator integration tests**

Run: `uv run pytest tests/unit/test_diagnostician.py tests/integration/test_orchestrator.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Commit message: `fix: classify transient Gemini failures for retry`

---

### Task 2: Store native Firestore timestamps and bound incident access

**Files:**
- Modify: `src/searcharis/models.py`
- Modify: `src/searcharis/storage/firestore.py`
- Modify: `src/searcharis/storage/memory.py`
- Modify: `src/searcharis/services/orchestrator.py`
- Modify: `src/searcharis/apps/ingress.py`
- Create or modify: `tests/unit/test_firestore_store.py`
- Modify: `tests/integration/test_orchestrator.py`

**Interfaces:**
- New optional `expires_at: datetime | None` on operational models or storage payloads.
- Store exposes `find_active_incident(repository: str, affected_url: str) -> IncidentRecord | None`.
- Store exposes `list_incidents(limit: int = 100) -> list[IncidentRecord]`.
- Existing legacy documents with ISO-string timestamps remain readable through Pydantic validation.

- [ ] **Step 1: Add failing serialization tests**

Use a fake Firestore document reference and assert `_set_model` receives Python `datetime` objects, not JSON strings.

```python
payload = fake_ref.last_set_payload
assert isinstance(payload["started_at"], datetime)
```

Add a legacy-read test where a snapshot contains ISO timestamp strings and `RunRecord.model_validate` still succeeds.

- [ ] **Step 2: Add failing bounded-query tests**

Test that `find_active_incident` queries repository and affected URL rather than calling `list_incidents()`. Test `list_incidents(limit=100)` applies ordering/limit rather than an unbounded stream.

- [ ] **Step 3: Implement native persistence helper**

Replace `model_dump(mode="json")` with `model_dump(mode="python")` for Firestore writes. Add a shared storage payload helper that sets `expires_at = now + timedelta(days=30)` for runs/evidence/actions/events while leaving incidents without TTL.

- [ ] **Step 4: Implement bounded incident query API in both stores**

Memory store filters in memory; Firestore store composes query filters on `repository`, `affected_url`, and active workflow states, then `limit(1)`. Public listing orders by `updated_at DESCENDING` and limits to 100.

- [ ] **Step 5: Update orchestrator/ingress callers**

Replace any `_find_open_incident()` collection scan with `find_active_incident(...)`. Keep behavior identical for duplicate detection.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/unit/test_firestore_store.py tests/integration/test_orchestrator.py -q`

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

Commit message: `refactor: bound Firestore queries and store native timestamps`

---

### Task 3: Add leased action claims and crash-safe GitHub reconciliation

**Files:**
- Modify: `src/searcharis/storage/firestore.py`
- Modify: `src/searcharis/storage/memory.py`
- Modify: `src/searcharis/integrations/github.py`
- Modify: `src/searcharis/services/orchestrator.py`
- Modify: `tests/integration/test_orchestrator.py`
- Create or modify: `tests/unit/test_github_broker.py`
- Create or modify: `tests/unit/test_action_claims.py`

**Interfaces:**
- Replace boolean-only claim with a lease-aware result, e.g. `ActionClaim(acquired: bool, stale_takeover: bool)`.
- `claim_action(idempotency_key, *, operation, incident_id, marker, lease_seconds=120)` creates or takes over only expired claims.
- GitHub broker adds read-only reconciliation helpers: `find_issue_by_marker`, `comment_exists`, `get_issue_state`.
- Marker format: `<!-- searcharis-action:<sha256-key> -->`.

- [ ] **Step 1: Add failing lease tests**

Cover:
- first claim acquired,
- concurrent unexpired claim denied,
- expired claim can be taken over,
- completed claim cannot be taken over,
- completion preserves result metadata.

- [ ] **Step 2: Add failing GitHub reconciliation tests**

Use HTTP mocks to prove:
- open reconciliation finds an existing issue containing the action marker and does not POST a second issue,
- verification comment reconciliation finds an existing marker and does not duplicate the comment,
- close reconciliation treats an already-closed issue as success.

- [ ] **Step 3: Implement lease payload atomically**

Firestore action document contains native timestamps `claimed_at`, `lease_expires_at`, `expires_at`, plus operation/incident/marker. Transaction allows takeover only when `status == claimed` and `lease_expires_at <= now`.

- [ ] **Step 4: Add deterministic markers to human-facing mutations**

Append the marker as an HTML comment to issue bodies and verification comments. It must not change the visible concise text.

- [ ] **Step 5: Reconcile only on stale takeover**

Normal first execution follows the existing path. A stale-takeover path performs the minimum GitHub read before deciding whether to repeat the mutation. If the side effect is already visible, call `complete_action` without repeating it.

- [ ] **Step 6: Add process-crash fault-injection integration tests**

Simulate “GitHub succeeded, completion record not written”, advance the injected clock past the lease, retry, and assert one external mutation total.

- [ ] **Step 7: Run focused tests**

Run: `uv run pytest tests/unit/test_action_claims.py tests/unit/test_github_broker.py tests/integration/test_orchestrator.py -q`

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

Commit message: `feat: reconcile stale GitHub action leases`

---

### Task 4: Configure Pub/Sub backoff, DLQ, and Firestore TTL/index policies

**Files:**
- Modify: `deployment/bootstrap.sh`
- Modify: `deployment/deploy-services.sh`
- Modify: `deployment/smoke.sh`
- Create: `firestore.indexes.json`
- Modify: `.github/workflows/deploy-gcp.yml`
- Add tests if shell-contract tests exist; otherwise use `bash -n` plus deployment assertions.

**Interfaces:**
- Topic: `searcharis-deployments-dead-letter`.
- DLQ retention subscription: `searcharis-deployments-dead-letter-retention`.
- Source push subscription retry: min 10s, max 60s.
- Source subscription DLQ max attempts: 8.
- TTL field: `expires_at` for operational collection groups.

- [ ] **Step 1: Add deployment smoke assertions first**

Extend `smoke.sh` to fail unless `gcloud pubsub subscriptions describe` shows:
- retryPolicy minimumBackoff 10s,
- maximumBackoff 60s,
- deadLetterPolicy topic suffix `/searcharis-deployments-dead-letter`,
- maxDeliveryAttempts 8.

Add checks that the DLQ retention subscription exists.

- [ ] **Step 2: Update bootstrap to create DLQ resources and IAM**

Idempotently create DLQ topic/subscription. Grant the Pub/Sub service agent `roles/pubsub.publisher` on the DLQ topic and `roles/pubsub.subscriber` on the source subscription/project scope required by the official configuration.

- [ ] **Step 3: Update push-subscription create/modify commands**

Use the supported `gcloud pubsub subscriptions create/update` flags for retry policy and dead-letter topic/attempts while preserving push endpoint and OIDC service account/audience.

- [ ] **Step 4: Add Firestore index definition**

Create the minimum composite index needed by `find_active_incident` and avoid indexes unrelated to the real query path.

- [ ] **Step 5: Enable TTL policies idempotently**

In the human-admin bootstrap path, run `gcloud firestore fields ttls update expires_at --collection-group=<group> --enable-ttl --async` for the operational collection groups. Do not block deployment waiting for TTL provisioning to finish; TTL enablement can take 10+ minutes.

- [ ] **Step 6: Verify shell syntax**

Run: `bash -n deployment/*.sh`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

Commit message: `feat: add PubSub backoff DLQ and Firestore TTL`

---

### Task 5: Make Cloud Run backpressure/resource settings explicit

**Files:**
- Modify: `deployment/deploy-services.sh`
- Modify: `deployment/deploy-demo-target.sh` only if the demo target currently lacks safe explicit bounds and a change is justified.
- Modify: `deployment/smoke.sh`

**Interfaces:**
- Ingress: 1 CPU, 512Mi, timeout 60s, max 3, concurrency 20, startup CPU boost.
- Worker: 1 CPU, 1Gi, timeout 600s, max 2, concurrency 4, startup CPU boost.

- [ ] **Step 1: Add smoke assertions before deploy changes**

Query service revision/template configuration and assert the expected CPU, memory, timeout, scaling, and concurrency values for ingress and worker.

- [ ] **Step 2: Apply explicit Cloud Run flags**

Add the official `gcloud run deploy` resource/timeout/startup-boost flags. Keep `--min=0`.

- [ ] **Step 3: Verify the worker ack/timeout contract**

Retain Pub/Sub ack deadline 600s and worker request timeout 600s; do not make ingress wait on worker execution.

- [ ] **Step 4: Run shell syntax checks**

Run: `bash -n deployment/*.sh`

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

Commit message: `chore: make Cloud Run production limits explicit`

---

### Task 6: Add judging-period no-teardown and secret-hygiene guardrails

**Files:**
- Modify: `README.md`
- Create: `docs/JUDGING-OPERATIONS.md`
- Modify: `deployment/smoke.sh`
- Modify: `.github/workflows/deploy-gcp.yml`

**Interfaces:**
- Canonical deadline string: `2026-10-01 23:45 PT`.
- No script/workflow deletes or disables the live services before this deadline.

- [ ] **Step 1: Add visible README warning**

Near live demo/deployment docs add a concise blockquote warning that judging access must stay online through October 1, 2026 11:45 PM PT and link to `docs/JUDGING-OPERATIONS.md`.

- [ ] **Step 2: Create judging operations runbook**

Document:
- public ingress URL,
- demo target URL,
- private worker requirement,
- `min=0` cost-safe behavior,
- health commands,
- “do not teardown before deadline”,
- safe post-judging teardown checklist,
- token-rotation procedure that never prints secret payloads.

- [ ] **Step 3: Add secret-version metadata warning**

Deployment/smoke lists enabled version names/states only. If more than one enabled `searcharis-github-token` version exists, print a warning and continue; never read payloads.

- [ ] **Step 4: Add a workflow summary reminder**

Every successful GCP deploy appends `KEEP LIVE THROUGH 2026-10-01 23:45 PT` and the public URL to `$GITHUB_STEP_SUMMARY`.

- [ ] **Step 5: Commit Task 6**

Commit message: `docs: add judging availability guardrails`

---

### Task 7: Repository submission metadata and persistent judging reminder

**Files:**
- No runtime code required unless connector limitations force documentation only.

**Interfaces:**
- Description: `Autonomous search-regression guardian built with Google ADK, Gemini and Google Cloud.`
- Homepage: `https://searcharis-ingress-2wzjcu6mqa-uc.a.run.app`
- Topics: `google-adk`, `gemini`, `vertex-ai`, `cloud-run`, `pubsub`, `firestore`, `ai-agents`, `seo`.

- [ ] **Step 1: Create persistent GitHub issue**

Title: `Keep Searcharis live through Oct 1 judging`

Body states the deadline, live URL, and that it should only be closed after judging ends.

- [ ] **Step 2: Attempt repository metadata mutation only through an exposed GitHub connector action**

If no repository-update action exists, do not invent one and do not use credentials from chat history. Record the exact three metadata values in `docs/JUDGING-OPERATIONS.md` and give the user the 30-second Settings instructions at handoff.

- [ ] **Step 3: Verify repository remains public and MIT licensed**

Read repository metadata; do not alter visibility/license.

---

### Task 8: Full verification, PR review, rollout, and real-cloud proof

**Files:**
- All files touched above.

**Interfaces:**
- Existing production invariant must remain unchanged.

- [ ] **Step 1: Run full local/CI-equivalent verification**

Run:

```bash
uv sync --locked
uv run ruff check src tests demo_target
bash -n deployment/*.sh
uv run pytest tests/unit tests/integration -q
```

Expected: all tests pass; no new application warnings/errors.

- [ ] **Step 2: Open PR `production-hardening` -> `main`**

PR description enumerates the six hardening areas and explicitly states no product-feature changes.

- [ ] **Step 3: Require green PR CI and inspect full diff**

Reject scope creep, secret payloads, unrelated refactors, or relaxed permissions.

- [ ] **Step 4: Merge only the reviewed green head SHA**

Use a merge commit to preserve task commits and provenance.

- [ ] **Step 5: Fast-forward `gcp-deploy` to the merged `main`**

Do not delete `gcp-deploy` because WIF trust is branch-restricted to it.

- [ ] **Step 6: Let the keyless GCP workflow deploy and run smoke assertions**

Verify WIF/OIDC, runtime URLs, private-worker invocation, Pub/Sub backoff/DLQ, Firestore policies where queryable, and Cloud Run resource settings.

- [ ] **Step 7: Run bounded five-duplicate live proof**

Required result:

```text
5 identical Pub/Sub events
 -> exactly 1 GitHub incident
 -> healthy restoration
 -> Cloud Tasks fresh verification
 -> exactly 1 verification comment
 -> exactly 1 close
 -> RESOLVED
```

- [ ] **Step 8: If any proof step fails, stop and debug the exact boundary**

Do not merge additional speculative fixes. Use Cloud Run/Firestore/GitHub evidence to identify the failing boundary first.

- [ ] **Step 9: Freeze runtime code after green proof**

Only submission copy/docs or blocking defect fixes are permitted afterward.
