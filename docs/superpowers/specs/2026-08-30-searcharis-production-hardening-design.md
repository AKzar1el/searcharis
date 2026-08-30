# Searcharis Production Hardening Design

Date: 2026-08-30
Status: Approved in chat; implementation pending written-spec review
Branch: `production-hardening`

## Goal

Harden the already-proven Searcharis GCP workflow for reliable judging and production-style operation without changing the core product architecture or demo behavior.

The existing invariant must remain true:

> duplicate deployment deliveries produce one incident; recovery is not trusted until a fresh external audit completes; successful verification produces one comment, one close, and final `RESOLVED` state.

## Scope

This pass addresses six bounded hardening areas around the current system:

1. transient Gemini/ADK failure classification and backoff,
2. Pub/Sub exponential retry and dead-letter delivery,
3. crash-safe reconciliation for GitHub side effects,
4. Firestore persistence/query/retention hardening,
5. explicit Cloud Run production controls,
6. judging-period operational guardrails and repository submission metadata.

No new agent platform, queue, database, orchestration framework, or general-purpose outbox service will be introduced.

## Current architecture preserved

The deployed flow remains:

```text
deployment event
  -> public Cloud Run ingress
  -> Pub/Sub
  -> private Cloud Run worker
  -> read-only hosted MCP validator
  -> Google ADK + Gemini 3.7 Flash on Vertex AI
  -> typed DiagnosisDecision
  -> deterministic policy gate
  -> narrow GitHub issue broker
  -> Firestore durable state
  -> Cloud Tasks delayed verification
  -> fresh live audit
  -> policy-gated comment/close
  -> RESOLVED
```

The hardening work changes failure semantics and operational guarantees around this flow; it does not replace the flow.

---

## 1. Gemini / ADK transient failure semantics

### Problem

The diagnostician currently retries all failures up to three times and ultimately exposes a generic `ValueError`. The orchestrator then treats diagnostician failures as `NEEDS_REVIEW`. This conflates malformed model output with transient provider/transport failures such as the real `429 RESOURCE_EXHAUSTED` observed during live stress testing.

### Design

Add two explicit error classes at the diagnostician boundary:

- `DiagnosticianRetryableError`
- `DiagnosticianInvalidOutputError`

Classify provider/transport failures as retryable when the underlying exception indicates HTTP/API status 408, 429, 500, 502, 503, or 504, or a recognized transient transport exhaustion/timeout condition.

Use bounded exponential backoff with jitter between attempts. The retry budget remains three model calls per workflow invocation so the worker cannot loop indefinitely.

Malformed structured output, schema validation failure, empty final response, or otherwise semantically invalid model output remains non-retryable at the infrastructure level and is surfaced as `DiagnosticianInvalidOutputError`.

### Orchestrator mapping

- `DiagnosticianRetryableError` -> `FAILED_RETRYABLE`
- `DiagnosticianInvalidOutputError` -> `NEEDS_REVIEW`
- unknown programming/configuration errors -> fail closed; do not authorize mutation

Returning `FAILED_RETRYABLE` from `/internal/pubsub` or `/internal/verify` continues to produce HTTP 500, allowing Pub/Sub or Cloud Tasks to redeliver.

### Safety

The deterministic policy gate is unchanged. A provider retry never itself authorizes an external mutation.

---

## 2. Pub/Sub exponential retry and dead-letter delivery

### Problem

The current push subscription uses Pub/Sub's default immediate redelivery. Repeated retryable downstream failures can therefore create a retry storm and unnecessary Gemini/provider load.

### Design

Keep the current authenticated push subscription and configure:

- minimum retry backoff: 10 seconds
- maximum retry backoff: 60 seconds
- dead-letter topic: `searcharis-deployments-dead-letter`
- maximum delivery attempts: 8

Create a pull subscription on the dead-letter topic so forwarded messages are retained for manual inspection instead of being published to an unsubscribed topic.

Grant the Google-managed Pub/Sub service agent the documented permissions needed to:

- publish to the dead-letter topic,
- consume/acknowledge delivery attempts on the source subscription.

The existing worker `run.invoker` identity remains unchanged.

### Rationale

Backoff smooths transient Vertex/GitHub/validator failures; eight attempts is enough to survive short provider incidents while keeping poison messages bounded. The exact Pub/Sub delivery-attempt count is best-effort, so application idempotency remains mandatory.

---

## 3. Crash-safe GitHub side-effect reconciliation

### Problem

Current Firestore action claims prevent concurrent duplicate mutations, but a process can theoretically die after GitHub accepted a mutation and before the action record is marked completed. A permanent `claimed` record could then suppress reconciliation forever.

### Design

Retain the current action-key/idempotency-key model and transactional Firestore claim, but evolve action documents into leased records:

```text
status: claimed | completed
claimed_at: timestamp
lease_expires_at: timestamp
operation: open | verification-comment | close | comment
incident_id: string
result_hash / mutation discriminator: string
result: optional object
expires_at: timestamp
```

A caller may take over a stale claim only after the lease expires.

Before repeating a stale external side effect, reconcile against GitHub:

- **open issue:** include a deterministic Searcharis action marker in the issue body and search the bounded repository issue set for that marker before creating a replacement.
- **verification comment:** include a deterministic marker in the comment body and inspect comments on the known issue before posting again.
- **close issue:** re-read known issue state; if already closed, record the action completed without closing again.
- **generic comment:** use the same marker/reconciliation approach if the path is retained.

Markers are machine-readable HTML comments so the human-facing issue remains concise, for example:

```html
<!-- searcharis-action:<sha256-key> -->
```

The GitHub broker gains only the minimum read operations needed for reconciliation; it does not gain repository-code mutation authority.

### Lease

Use a short bounded lease (proposed: 2 minutes), longer than normal GitHub request duration but short enough to recover during provider/process failures.

### Safety

A stale claim never directly authorizes mutation. It only enables reconciliation; normal deterministic policy must already have allowed the action for the current run.

---

## 4. Firestore persistence, query, and retention hardening

### Problem

The current Firestore adapter serializes models with `model_dump(mode="json")`, storing datetimes as strings. `list_incidents()` also scans the entire incidents collection. Operational run/evidence/action records have no retention policy.

### Design

### 4.1 Native Firestore values

Persist with Python/native Pydantic values so datetime objects are written as Firestore timestamp values rather than JSON strings. Reads remain Pydantic-validated.

Existing historical string documents must remain readable. No destructive migration is required for hackathon history.

### 4.2 Bounded incident lookup

Replace `_find_open_incident()`'s full collection scan with a store method that queries by the fields already known from the deployment event:

- repository
- affected URL
- unresolved state set

Because Firestore may require a composite index, commit the index definition/configuration and provision it in deployment bootstrap where practical.

Public incident listing must be bounded and ordered, e.g. latest 100 by `updated_at`, rather than streaming an unlimited collection.

### 4.3 TTL

Add native `expires_at` timestamps to ephemeral operational documents:

- runs
- evidence
- action/idempotency records

Preserve `incidents` as durable audit history and preserve deployment events long enough for scheduled verification. Do not TTL any record that can still be referenced by an outstanding Cloud Task.

Proposed retention:

- runs: 30 days
- evidence: 30 days
- actions: 30 days
- events: 30 days or longer if implementation simplicity favors one uniform operational TTL
- incidents: no TTL

Enable Firestore TTL policies for the selected collection groups on `expires_at`. Add single-field index exemptions for TTL fields because they are not queried.

### Compatibility

New reads must support both legacy string timestamps and native Firestore timestamps during the transition.

---

## 5. Cloud Run production controls

### Goal

Make resource/cost/backpressure behavior explicit while avoiding aggressive scaling that can amplify Gemini/provider load.

### Ingress

- min instances: 0
- max instances: 3
- concurrency: 20
- CPU: 1 vCPU
- memory: 512 MiB
- request timeout: 60 seconds
- startup CPU boost: enabled

### Worker

- min instances: 0
- max instances: 2
- concurrency: 4
- CPU: 1 vCPU
- memory: 1 GiB
- request timeout: 600 seconds
- startup CPU boost: enabled

### Rationale

The ingress performs bounded validation/publishing and can tolerate higher concurrency. The worker performs external MCP calls, Gemini inference, Firestore operations, and GitHub/Tasks mutations, so lower concurrency and two max instances provide deliberate downstream backpressure.

The worker timeout remains compatible with the Pub/Sub push acknowledgment deadline. Scale-to-zero is retained to control judging-period cost while keeping the service continuously addressable.

No minimum warm instance is required for judging; cold-start performance is mitigated with startup CPU boost.

---

## 6. Judging-period and submission guardrails

### Availability invariant

The deployed project must remain available through the end of the official judging period:

**October 1, 2026 at 11:45 PM Pacific Time.**

### Repository guardrails

Add:

- a prominent README judging-period warning near deployment/demo instructions,
- `docs/JUDGING-OPERATIONS.md` with the live URLs, no-teardown rule, health checks, secret-rotation notes, budget-safe settings, and post-judging teardown date,
- a persistent GitHub issue titled approximately `Keep Searcharis live through Oct 1 judging`, closed only after the judging period,
- a deployment validation step that fails if service configuration would make the public app unavailable during judging,
- no automatic teardown workflow before the judging deadline.

### Secret hygiene

Deployment/preflight should verify that at least one enabled GitHub-token version exists and should warn when multiple enabled versions exist. It must never read or print the payload. The known invalid version 1 should be disabled by the human administrator if it is still enabled.

### Repository metadata

Target repository metadata:

- Description: `Autonomous search-regression guardian built with Google ADK, Gemini and Google Cloud.`
- Homepage: `https://searcharis-ingress-2wzjcu6mqa-uc.a.run.app`
- Topics: `google-adk`, `gemini`, `vertex-ai`, `cloud-run`, `pubsub`, `firestore`, `ai-agents`, `seo`

If the available GitHub connector cannot mutate repository metadata, document the exact values and leave application code unchanged.

---

## Error-handling matrix

| Boundary | Error class | Workflow behavior |
|---|---|---|
| Validator | transient/provider failure | `FAILED_RETRYABLE`, Pub/Sub/Tasks retry |
| Validator | malformed evidence/run mismatch | terminal/fail closed |
| Gemini/ADK | 408/429/5xx/transient transport | bounded local backoff, then `FAILED_RETRYABLE` |
| Gemini/ADK | invalid structured response | `NEEDS_REVIEW` |
| Policy | evidence insufficient/stale/mismatched | deny or review, never mutate |
| GitHub | transient API failure | release/lease action and `FAILED_RETRYABLE` |
| GitHub | crash after success | stale lease -> reconcile -> mark complete or safely repeat |
| Cloud Tasks | transient enqueue failure | `FAILED_RETRYABLE`, no duplicate GitHub issue |
| Pub/Sub | repeated worker failure | exponential redelivery -> DLQ after bounded attempts |

---

## Testing strategy

Implementation must be test-driven.

### Unit tests

Add tests for:

- transient Gemini status classification,
- exponential-backoff retry budget without real sleeping by injecting clock/sleep/random dependencies,
- invalid structured output -> review semantics,
- leased action claim/takeover behavior,
- stale GitHub open reconciliation,
- stale comment reconciliation,
- already-closed reconciliation,
- native Firestore datetime serialization helpers,
- TTL calculation,
- bounded incident query behavior.

### Integration tests

Retain all existing tests, especially:

- duplicate deployment -> one issue,
- concurrent 200-way duplicate delivery -> one issue,
- concurrent 200-way recovery -> one comment/close,
- GitHub-open retry,
- scheduler retry,
- GitHub-close retry,
- verification cannot close while the triggering finding remains.

Add fault-injection tests specifically for process-crash reconciliation states.

### Deployment tests

CI continues to require:

- locked dependency install,
- Ruff,
- shell syntax,
- full unit/integration suite.

The GCP deployment workflow must then prove:

1. WIF/OIDC authentication,
2. demo target/worker/ingress deployment,
3. public ingress readiness,
4. private worker authenticated readiness,
5. healthy target,
6. Pub/Sub publish,
7. incident API,
8. subscription retry/DLQ configuration,
9. Cloud Run resource settings,
10. Firestore TTL/index configuration where available.

Finally rerun the bounded real-cloud proof:

```text
5 identical Pub/Sub events
 -> exactly 1 GitHub incident
 -> healthy restoration
 -> Cloud Tasks fresh verification
 -> exactly 1 verification comment
 -> exactly 1 close
 -> RESOLVED
```

No merge to `main` occurs unless this passes.

---

## Rollout

1. Implement on `production-hardening` only.
2. Run PR CI.
3. Review diff for scope creep and secrets.
4. Merge only green code into `main`.
5. Fast-forward/update `gcp-deploy` without losing history so the existing WIF branch restriction remains valid.
6. Deploy via the existing keyless GitHub Actions workflow.
7. Run cloud smoke tests.
8. Run the bounded live proof.
9. Freeze runtime code for submission unless a blocking defect appears.

Rollback is the previous proven Cloud Run revision / previous Git commit; no destructive Firestore migration is permitted in this pass.

---

## Non-goals

This pass will not add:

- Kubernetes/GKE,
- Redis,
- a replacement message broker,
- a generalized saga/outbox framework,
- Terraform/IaC rewrite,
- Gemini Enterprise Agent Platform migration,
- new user-facing product features,
- multi-tenant billing/auth,
- new AI models for bonus points,
- broad GitHub repository write permissions.

---

## Success criteria

The pass is complete only when all of the following are true:

- current 48-test baseline remains green and new hardening tests pass,
- transient Gemini failures are explicitly retryable with bounded backoff,
- malformed model output fails closed to human review,
- Pub/Sub push uses exponential retry and a configured DLQ,
- dead-letter IAM and retention subscription are valid,
- stale external-action claims can reconcile after process failure,
- Firestore stores new timestamps natively and operational records have bounded retention,
- incident lookup/listing is bounded,
- Cloud Run resource/concurrency/timeouts are explicit and verified,
- judging-period no-teardown warning is obvious in repository operations docs,
- the live app remains available through October 1, 2026 11:45 PM PT,
- the real five-duplicate GCP proof still resolves exactly one incident end to end.
