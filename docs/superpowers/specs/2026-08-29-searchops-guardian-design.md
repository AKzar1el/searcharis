# SearchOps Guardian — Hackathon Design

> Working name. The contest FAQ recommends entrants choose a human-generated final project name before submission.

Date: 2026-08-29
Track: Taskmaster
Status: Approved architecture, pre-implementation

## 1. Objective

Build a new autonomous post-deployment search-regression responder for the All Things Agentic Hackathon. The system reacts to a deployment event, audits the deployed public site, uses Gemini to diagnose material search/technical regressions, applies deterministic policy gates, creates or updates a GitHub incident, and independently re-verifies the live site before resolving the incident.

The central product claim is narrow and testable:

> A deployment can silently damage search visibility. This system detects the regression, routes the right action, and refuses to mark it fixed until external evidence proves recovery.

## 2. Competition Compliance

The submitted project must be new during the contest submission period. Pre-existing DigestSEO MCP services may be consumed as disclosed external dependencies, but their pre-existing implementation is not claimed as hackathon work.

Required contest stack:

- Gemini 3.5 or newer, via Gemini API or Vertex AI.
- At least one Google agent framework. This design uses Google ADK.
- At least one Google Cloud infrastructure service. This design uses Cloud Run, Pub/Sub, Firestore, and Cloud Tasks.

Submission artifacts to produce before deadline:

- One selected category: Taskmaster.
- Hosted project URL if available; strongly preferred.
- Written project description covering features, technologies, data sources, findings, and learnings.
- GitHub/GitLab/Bitbucket repository URL.
- README spin-up instructions.
- Architecture diagram.
- Public YouTube or Vimeo demo video, English or English-subtitled, maximum four minutes.
- Demo must visibly prove backend execution on Google Cloud.

## 3. Scope

### Must ship

1. Deployment-event ingress.
2. Event normalization and authentication.
3. Asynchronous execution via Google Cloud.
4. Public-site audit through the hosted Web Validator MCP.
5. Gemini diagnosis through Google ADK.
6. Typed structured decision output.
7. Deterministic policy gate between model output and mutations.
8. GitHub issue create/comment/close lifecycle.
9. Persistent incident/run/evidence state in Firestore.
10. Delayed or event-triggered verification.
11. Independent re-audit before incident closure.
12. Small incident timeline/status UI or equivalent judge-visible state surface.
13. Controlled demo target capable of switching between broken and fixed deployments.
14. Tests for state transitions, policy gate, idempotency, and GitHub mutation boundaries.
15. Deployment documentation, architecture diagram, and contest disclosures.

### Optional only after core is complete

- Search Console read-only enrichment through pre-existing mcp-gsc.
- TrendPulse context enrichment.
- Additional observability/OpenTelemetry.
- Secondary model verification.
- Public article/social-post bonus work.

### Explicitly out of scope

- Multi-agent fleet.
- Generic chat assistant.
- Automatic arbitrary source-code edits.
- CMS integrations.
- Billing/subscriptions.
- Full DigestSEO product integration.
- RAG/vector database.
- Long-term conversational memory.
- Generic analytics dashboard.
- Broad keyword/content-generation suite.

## 4. Architecture

### 4.1 Components

#### Ingress service — Cloud Run

Responsibilities:

- Receive GitHub deployment/webhook event or controlled demo event.
- Verify webhook signature for real GitHub events.
- Normalize input into an immutable deployment event.
- Persist the received event envelope.
- Publish work to Pub/Sub.
- Acknowledge quickly; never hold the webhook open for the full workflow.

No model execution occurs in the request path.

#### Pub/Sub

Responsibilities:

- Decouple webhook reception from agent execution.
- Deliver normalized work to the worker.
- Permit retries/redelivery.

Because delivery can repeat, downstream actions must be idempotent.

#### Worker — Cloud Run + Google ADK

Responsibilities:

- Load the event and current incident state.
- Call read-only audit tools.
- Construct bounded evidence.
- Invoke Gemini for diagnosis and routing.
- Validate model output against a strict schema.
- Pass proposed action through the deterministic policy engine.
- Request allowed GitHub mutations via the action broker.
- Persist run state and evidence.
- Schedule verification when needed.

#### Gemini

Role:

- Interpret audit findings in deployment context.
- Classify materiality/severity.
- Produce a structured recommendation.
- Explain which evidence supports the decision.

Gemini does not directly hold generic GitHub mutation authority.

Proposed decision contract:

```json
{
  "classification": "REGRESSION|RECOVERY|NO_ACTION|NEEDS_REVIEW",
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "finding_codes": ["string"],
  "affected_urls": ["https://..."],
  "evidence_ids": ["string"],
  "proposed_action": "OPEN_INCIDENT|COMMENT|CLOSE_INCIDENT|RECHECK|ESCALATE|NONE",
  "summary": "string",
  "reasoning_summary": "string"
}
```

#### Web Validator MCP — pre-existing external dependency

Core audit provider for the hackathon project.

Use only read-oriented hosted capabilities necessary to inspect the deployed public site. The hackathon repository will document that this MCP server predates the contest and is consumed as an external dependency.

#### GSC MCP — optional pre-existing external dependency

May enrich diagnosis with Search Console information where OAuth is already available. It must not be required for the deterministic demo path.

#### Policy engine

Pure deterministic code.

Responsibilities:

- Reject actions unsupported by evidence.
- Require fresh evidence for mutations.
- Enforce the allowed mutation matrix.
- Deduplicate incidents.
- Prevent close-without-verification.
- Enforce severity thresholds where applicable.
- Route ambiguous outputs to NEEDS_REVIEW rather than guessing.

Invariant:

> No incident may transition to RESOLVED solely because Gemini says the problem is fixed. A fresh external audit must show the triggering finding is absent.

#### GitHub action broker

Owns the smallest possible mutation surface:

- create issue
- add comment
- close issue

No arbitrary repository writes, merges, branch pushes, secret changes, or broad GitHub tool exposure.

#### Firestore

Stores workflow state, not conversational memory.

Collections/logical entities:

- `sites`
- `events`
- `runs`
- `incidents`
- `evidence`
- `actions`

Important stored data:

- immutable event identifiers
- incident fingerprint
- current workflow state
- evidence references/digests
- action idempotency keys
- GitHub issue identifier
- timestamps and retry metadata

#### Cloud Tasks

Schedules delayed verification calls where a fixed deployment needs time to become publicly observable.

Verification may also be triggered directly by a subsequent deployment event. The demo should prefer the simplest reliable path.

#### Minimal UI

Purpose is judge comprehension, not product polish.

Show:

- target site
- latest deployment/event
- workflow state
- detected findings
- model classification
- policy result
- GitHub incident link/number
- verification result
- final status

Do not build a general chat interface.

## 5. Workflow State Machine

Primary path:

`RECEIVED -> AUDITING -> DECIDED -> ACTIONED -> VERIFYING -> RESOLVED`

Non-happy states:

- `NEEDS_REVIEW`
- `FAILED_RETRYABLE`
- `FAILED_TERMINAL`

State transitions must be explicit and validated. A repeated event or retry must not duplicate irreversible actions.

## 6. Idempotency

Derive a stable incident fingerprint from the target and finding identity, for example:

`SHA256(repository + target_origin + affected_url + finding_code)`

Use separate action idempotency keys for:

- issue creation
- issue comment for a given evidence set
- issue closure for a given verification run

Before mutating GitHub, check persisted action state transactionally where practical.

## 7. Audit and Evidence Model

Evidence is external and immutable per run.

Each evidence record should include:

- evidence ID
- run ID
- provider/tool
- target URL
- finding code/category
- normalized details
- retrieved timestamp
- content/result hash where practical

The model receives a bounded evidence package rather than unrestricted tool output.

## 8. Security Boundaries

- Verify GitHub webhook HMAC for real webhook ingress.
- Use least-privilege service accounts between Google Cloud components.
- Require authenticated Cloud Run-to-Cloud Run / Cloud Tasks calls where applicable.
- Store secrets in Google Secret Manager or equivalent Google Cloud secret mechanism, never repository files.
- Keep public-site audit tools read-only.
- Do not expose arbitrary URL fetching beyond the existing validator's authorization and SSRF controls.
- GitHub broker credentials get only the permissions needed for issues.
- Do not log OAuth tokens, API keys, webhook secrets, or complete sensitive headers.

## 9. Failure Handling

### Audit provider unavailable

- Mark `FAILED_RETRYABLE` if transient.
- Do not create/close incidents from missing evidence.
- Retry with bounded attempts/backoff.

### Gemini unavailable or invalid structured response

- Retry boundedly.
- If still invalid, move to `NEEDS_REVIEW` or `FAILED_RETRYABLE` according to cause.
- Never fall back to an unvalidated free-text mutation decision.

### GitHub mutation fails

- Persist intent and failure.
- Retry idempotently.
- Do not advance to `ACTIONED` until the broker confirms the external mutation.

### Verification still fails

- Keep incident open.
- Comment only if useful and deduplicated.
- Schedule another bounded check or await next deployment.

### Duplicate Pub/Sub delivery

- Rehydrate state by event/idempotency key.
- Return success without re-running already completed irreversible actions unless a recheck is explicitly required.

## 10. Demo Scenario

Controlled public demo target has two deployable states.

Broken version includes one highly legible search regression, preferably `noindex` on an indexable page. A secondary structural finding may exist, but the main demo should center on one obvious regression.

Demo sequence:

1. Show healthy/baseline target briefly.
2. Trigger broken deployment.
3. Show event arrival on Google Cloud.
4. Show asynchronous worker run.
5. Show validator evidence proving the live URL is broken.
6. Show Gemini structured diagnosis.
7. Show deterministic policy approval.
8. Show GitHub issue created automatically.
9. Show Firestore/UI incident state OPEN.
10. Trigger fixed deployment.
11. Show fresh audit proving the finding disappeared.
12. Show policy allowing closure only after that proof.
13. Show GitHub verification comment and automatic close.
14. Show incident RESOLVED.
15. Briefly show Cloud Run / Google Cloud proof required by contest.

The final video must remain under four minutes.

## 11. Testing Strategy

### Unit tests

- decision schema validation
- policy allow/deny matrix
- state transition validity
- incident fingerprint stability
- action idempotency
- verification-before-close invariant
- severity/action mapping

### Integration tests

Use fakes for Gemini, validator, Firestore, and GitHub where possible so CI is deterministic.

Cover:

- broken deployment opens exactly one incident
- duplicate event opens zero additional incidents
- fixed deployment with fresh evidence closes the existing incident
- model proposes CLOSE but audit still fails -> close denied
- model returns malformed output -> no mutation
- provider outage -> no unsupported mutation

### Live smoke tests

A controlled target may be exercised against deployed Cloud Run and the hosted validator before recording the demo.

## 12. Repository Boundaries and Disclosure

The new hackathon repository contains the work created for this contest:

- ingress
- orchestration
- ADK agent
- Gemini integration
- policy engine
- persistence
- GitHub action lifecycle
- verification logic
- demo target
- minimal UI
- tests
- Google Cloud deployment configuration
- documentation

Pre-existing external dependencies to disclose:

- `AKzar1el/mcp-web-validator`
- `AKzar1el/mcp-gsc` if used
- `AKzar1el/mcp-trendpulse` if used

The hackathon submission must not imply those pre-existing servers were created during the contest.

## 13. Submission Narrative

Primary track: Taskmaster.

BYOF framing:

Search regressions after website changes are easy to introduce and tedious to verify manually. The system removes the repetitive loop of noticing a release, auditing production, interpreting findings, opening a precise engineering incident, waiting for the next deployment, and checking again before closure.

Twist:

The agent may recommend that a regression is fixed, but it has no authority to resolve the incident until a fresh external audit independently proves recovery.

## 14. Acceptance Criteria

The project is submission-ready only if all are true:

1. A new deployment event can trigger the workflow without human intervention.
2. The live target is audited through a real external tool.
3. Gemini is invoked through the required Google stack and returns structured output.
4. A deterministic gate controls every GitHub mutation.
5. A broken deployment opens exactly one GitHub incident.
6. Duplicate delivery does not duplicate the incident.
7. A model-only assertion of recovery cannot close the incident.
8. A fresh successful audit can cause the incident to be verified and closed.
9. State survives process restarts through Firestore.
10. Google Cloud deployment is demonstrable.
11. README contains reproducible spin-up/deployment instructions.
12. Architecture diagram exists.
13. Pre-existing dependencies are clearly disclosed.
14. Demo can be completed coherently in under four minutes.

## 15. Deferred Product Opportunities

After the hackathon, if useful:

- Search Console performance anomaly detection as an independent trigger.
- TrendPulse demand context.
- pull-request annotations before deployment.
- safe automated patch proposals requiring human approval.
- multi-site DigestSEO product integration.
- user/account/billing layer.

These are intentionally excluded from the contest MVP.
