# Searcharis — four-minute demo script

Target length: **3:45–3:55**. Keep the recording continuous. AI narration is acceptable; the screen must show the real application state changing.

## 0:00–0:20 — problem and promise

Narration:

> A deployment can be green while search visibility is already broken. Searcharis watches successful deployments, audits the live page, lets Gemini classify the evidence, and opens a GitHub incident only through deterministic policy. It will not close that incident until a fresh external audit proves the regression is gone.

Show the architecture diagram for no more than ten seconds.

## 0:20–0:45 — prove the real Google Cloud system

Show:

- Cloud Run services: ingress, private worker, demo target
- Pub/Sub topic/push subscription
- Firestore database
- Cloud Tasks queue
- worker configuration showing `gemini-3.7-flash` / Vertex AI without exposing secrets

Open the healthy demo target and briefly show the page title.

## 0:45–1:50 — introduce a real regression and let Searcharis act

Run the pre-prepared command that sends 100% demo traffic to tag `broken`.

Refresh the target and show that the document title is now absent. Trigger the controlled deployment event.

Then show, in sequence:

1. Cloud Run worker request/log entry.
2. Searcharis timeline changing through audit/decision/action states.
3. Normalized evidence `seo.missing_title`.
4. Gemini decision `REGRESSION / HIGH / OPEN_INCIDENT`.
5. Policy result `ALLOW_OPEN`.
6. The GitHub issue appearing automatically with the evidence ID and deployment SHA.

Do not linger on generated prose. The visible state transition is the proof.

## 1:50–2:35 — explain why the agent cannot hallucinate a fix

Show the policy test or small code excerpt containing the closure invariant.

Narration:

> Gemini diagnoses; it does not own GitHub. The mutation broker exposes only issue create, comment, and close. Every action has an idempotency key, and closure additionally requires `validator.audit_complete` from a fresh run with the original triggering code absent. A model saying “fixed” is insufficient.

Optionally show the duplicate-delivery test result: one event delivered twice, one issue.

## 2:35–3:25 — recovery and independent verification

Switch demo traffic back to tag `healthy`.

Trigger the next deployment event or the verification path. Show:

1. New live audit completes.
2. `seo.missing_title` is absent.
3. Gemini recommends `RECOVERY / CLOSE_INCIDENT`.
4. Policy returns `ALLOW_CLOSE`.
5. Searcharis posts a verification comment containing the fresh result hash.
6. The GitHub issue closes.
7. Timeline becomes `RESOLVED`.

## 3:25–3:55 — architecture, disclosure, close

Return to the architecture diagram.

Narration:

> Searcharis uses Google ADK with Gemini 3.7 Flash on Vertex AI, Cloud Run, Pub/Sub, Firestore, Cloud Tasks, and Secret Manager. The Web Validator MCP is my pre-existing open-source read-only dependency and is disclosed as such; the orchestration, Google Cloud workflow, policy, incident lifecycle, verification system, demo target, and UI were built for this hackathon.

End on the Searcharis timeline with the incident visibly resolved.
