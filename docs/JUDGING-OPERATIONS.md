# Searcharis Judging Operations

## Availability rule

> **DO NOT disable, delete, suspend, or make Searcharis inaccessible before October 1, 2026 at 11:45 PM PT.**

The All Things Agentic Hackathon judging period requires the submitted project to remain available for testing. Scale-to-zero is intentional and safe: Cloud Run may have zero warm instances while idle, but the service URLs must continue to resolve and start on demand.

## Live endpoints

- Public Searcharis app: https://searcharis-ingress-2wzjcu6mqa-uc.a.run.app
- Controlled demo target: https://searcharis-demo-target-2wzjcu6mqa-uc.a.run.app
- Worker: private Cloud Run service `searcharis-worker`; do not make it public.

## Required live resources

Keep these resources available through October 1, 2026 at 11:45 PM PT:

- Cloud Run: `searcharis-ingress`, `searcharis-worker`, `searcharis-demo-target`
- Pub/Sub: `searcharis-deployments`, `searcharis-worker-push`, dead-letter topic and retention subscription
- Firestore `(default)` database and production index/TTL policies
- Cloud Tasks queue `searcharis-verification`
- Secret Manager runtime secrets
- Vertex AI access for Gemini 3.7 Flash

Do not run teardown/delete commands during judging. Do not disable billing or the APIs required by these resources.

## Cost-safe production settings

The submitted deployment intentionally uses Cloud Run `min=0`, so idle services scale to zero. Runtime capacity is bounded:

- ingress: max 3 instances, concurrency 20, 1 vCPU, 512 MiB, 60-second timeout
- worker: max 2 instances, concurrency 4, 1 vCPU, 1 GiB, 600-second timeout

The worker cap/concurrency provide downstream backpressure for Gemini, the validator, GitHub, Firestore, and Cloud Tasks.

## Health checks

```bash
export GOOGLE_CLOUD_PROJECT=searcharis
export GOOGLE_CLOUD_LOCATION=us-central1

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

INGRESS_URL="$(gcloud run services describe searcharis-ingress \
  --region="$GOOGLE_CLOUD_LOCATION" --format='value(status.url)')"
DEMO_URL="$(gcloud run services describe searcharis-demo-target \
  --region="$GOOGLE_CLOUD_LOCATION" --format='value(status.url)')"

curl -fsS "$INGRESS_URL/ready"
curl -fsS "$DEMO_URL/" | grep -i '<title>'
gcloud run services describe searcharis-worker \
  --region="$GOOGLE_CLOUD_LOCATION" >/dev/null
```

For a complete authenticated infrastructure check, use the keyless `gcp-deploy` workflow or `deployment/smoke.sh` with an audience-bound worker identity token.

## Secret hygiene

Never print runtime secret payloads into terminals, CI output, screenshots, issues, or the demo video.

Check only metadata:

```bash
gcloud secrets versions list searcharis-github-token \
  --project=searcharis \
  --format='table(name,state,createTime)'
```

The validated GitHub runtime token is Secret Manager version 2. The known invalid version 1 should remain disabled. If version 1 is still enabled, a human project administrator should run:

```bash
gcloud secrets versions disable 1 \
  --secret=searcharis-github-token \
  --project=searcharis
```

Token rotation procedure:

1. Validate the new fine-grained PAT against `GET /user` and a non-mutating/validation issue-write probe.
2. Add it as a new Secret Manager version without printing it.
3. Redeploy the worker so the `latest` environment-secret binding is resolved at instance startup.
4. Run the cloud smoke test and bounded live proof.
5. Disable the superseded version only after the new revision is proven.

## Firestore admin hardening

`deployment/bootstrap.sh` is the human-admin path for enabling operational TTL policies and provisioning the incident composite index. The GitHub WIF deployer intentionally does not receive Firestore schema-administration privileges.

After a schema-related change, run the bootstrap once from an authenticated Cloud Shell and allow asynchronous index/TTL provisioning to finish before the final live proof.

## Submission repository metadata

Set these values in GitHub repository **About**:

- Description: `Autonomous search-regression guardian built with Google ADK, Gemini and Google Cloud.`
- Website: `https://searcharis-ingress-2wzjcu6mqa-uc.a.run.app`
- Topics: `google-adk`, `gemini`, `vertex-ai`, `cloud-run`, `pubsub`, `firestore`, `ai-agents`, `seo`

Keep the repository public and MIT licensed through judging.

## Recovery / rollback

If a new deployment is unhealthy, do not delete the services. Roll traffic/configuration back to the last proven Cloud Run revision or redeploy the last proven Git commit. Preserve Firestore data and the public `run.app` endpoint.

The controlled demo target must be returned to the `healthy` tagged revision after every test or recording attempt:

```bash
gcloud run services update-traffic searcharis-demo-target \
  --region=us-central1 \
  --to-tags=healthy=100 \
  --quiet
```

## Post-judging teardown

Only **after October 1, 2026 at 11:45 PM PT** and after confirming no organizer extension is in effect:

1. Archive the final demo evidence and URLs.
2. Disable/revoke the dedicated GitHub PAT.
3. Disable or delete unused secrets.
4. Delete or scale down hackathon-only Cloud Run/Pub/Sub/Cloud Tasks resources if no longer useful.
5. Keep the repository and architectural evidence public if desired.

Until then, the default action is: **keep Searcharis live.**
