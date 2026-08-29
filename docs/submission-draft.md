# Devpost submission draft — Searcharis

## Project name

**Searcharis**

## Tagline

**Autonomous Search Regression Guardian**

## Track

**Taskmaster**

## Inspiration / problem

While maintaining and repeatedly deploying search-sensitive websites, I kept hitting the same post-deploy chore: re-check the production page, confirm that search-facing metadata and structured signals survived the release, diagnose anything that changed, open the right engineering follow-up, and then remember to verify it again after the fix. It is repetitive enough to skip and important enough that skipping it can turn a tiny deployment mistake into delayed search damage.

Production deployment systems usually have a binary success signal: the service started and the health check passed. Search regressions do not respect that boundary. A deploy can silently remove a title, canonical, viewport declaration, structured data, or other search-facing contract and remain “healthy” until traffic damage appears later.

Searcharis turns that personal release-check loop into an autonomous operational workflow. A successful deployment becomes an event; the live site is audited immediately; Gemini interprets bounded evidence; deterministic policy decides whether an incident may be opened; and the incident remains open until fresh external evidence proves the original regression is gone.

## What it does

Searcharis:

1. receives a signed GitHub deployment-status event or a controlled demo event;
2. publishes the normalized event to Pub/Sub;
3. audits the live public page through a read-only validator service;
4. persists immutable normalized evidence in Firestore;
5. asks a Google ADK agent using Gemini 3.7 Flash for a typed diagnosis;
6. passes that recommendation through a deterministic policy gate;
7. creates or updates a GitHub issue through a broker that exposes only three mutation operations;
8. schedules authenticated re-verification with Cloud Tasks; and
9. closes the issue only when a new completed audit no longer contains the triggering finding.

## Why this is agentic rather than a cron script

The workflow is event-driven and crosses several applications. The model is responsible for interpreting a changing evidence packet in deployment context and routing the appropriate next action, while deterministic infrastructure constrains authority and verifies side effects. The system can distinguish regression, recovery, no-action, and needs-review outcomes, maintain durable state across retries, and continue the incident lifecycle after the original request has ended.

## Google technology

- **Google ADK** — single diagnostician agent with a strict structured output schema
- **Gemini 3.7 Flash via Vertex AI** — evidence interpretation and action recommendation
- **Cloud Run** — public ingress, private worker, controlled demo target
- **Pub/Sub** — asynchronous deployment-event delivery
- **Firestore** — workflow/evidence/incident/idempotency persistence
- **Cloud Tasks** — delayed authenticated verification
- **Secret Manager** — operational secrets

## Data sources and integrations

- **Public webpage audit evidence** from the pre-existing read-only `mcp-web-validator` service.
- **GitHub deployment metadata** for the repository, commit, deployed environment URL, and delivery identity.
- **GitHub Issues** as the bounded external action surface for create/comment/close.

The submitted core workflow does not require Google Search Console, Analytics, TrendPulse, customer data, or private website content.

## Architectural discipline

The model never receives a generic GitHub mutation tool. It can recommend an action, but only typed policy code can authorize a mutation. The GitHub broker exposes exactly create issue, comment on issue, and close issue.

All irreversible actions use stable SHA-256 idempotency keys. Cloud Tasks use deterministic hashed task IDs. Retryable worker failures are intentionally returned to Pub/Sub as HTTP 500 so delivery can be retried without duplicating external actions.

The strongest invariant is verification-before-close: `CLOSE_INCIDENT` requires a fresh `validator.audit_complete` record and the triggering finding code must be absent from that same audit. Model output alone can never produce `RESOLVED`.

## Validation before cloud deployment

The public CI pipeline installs the project from a clean runner, runs Ruff, validates the deployment shell scripts, and executes the unit/integration suite. The suite contains 45 tests, including 200-way concurrent duplicate deployment deliveries, 200-way concurrent recovery verifications, verification-before-close checks, ingress authentication/allowlisting, and injected failures for GitHub open/close and Cloud Tasks scheduling.

The final Google Cloud deployment and live end-to-end proof are intentionally treated as a separate gate rather than inferred from local tests.

## Demo

The controlled demo target is designed to deploy as two tagged Cloud Run revisions. The healthy revision contains a valid title. The broken revision removes only the title while retaining the rest of the page, producing a deterministic `seo.missing_title` error.

The final video will show the live target becoming broken, a deployment event entering Google Cloud, evidence being produced, Gemini routing the incident, deterministic policy authorizing one GitHub issue, then the healthy revision returning and a fresh audit independently verifying recovery before the issue closes. The video will also show visible Google Cloud execution evidence.

## Pre-existing work disclosure

Searcharis was created during the All Things Agentic Hackathon submission period.

It consumes the pre-existing open-source `AKzar1el/mcp-web-validator` service as an external read-only audit dependency. That service predates the hackathon and is not claimed as hackathon-built work. All Searcharis orchestration, ADK/Gemini logic, Google Cloud event processing, persistence, policy enforcement, GitHub incident lifecycle, verification workflow, demo target, and UI in this repository were created for the hackathon.

## What I learned

The useful boundary in an agentic production system is not “LLM versus deterministic code”; it is **interpretation versus authority**. Gemini is excellent at interpreting evidence and context. The safest place for irreversible authority is a small typed policy layer with observable external verification. Designing that boundary also made retries, idempotency, and the demo substantially clearer.
