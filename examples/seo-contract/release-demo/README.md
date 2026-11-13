# Offline HTML + sitemap release gate demo

Run this command from the Searcharis repository root (Python 3.11+ and
`uv` required). It reuses the checked-in **compiled two-page Vite HTML**
snapshot instead of building or fetching a website:

```sh
uv run python -m searcharis.release_cli examples/seo-contract/release-demo/manifest.json examples/seo-contract/generator-fixtures/vite/artifacts examples/seo-contract/release-demo/sitemap.xml --require-full-coverage --require-indexability-coverage --check-canonical-consistency --json
```

**Expected exit code: 0.** Both built pages satisfy the reviewed title,
language, canonical and indexability contracts, every built HTML page is
covered, and the supplied local sitemap lists both required URLs.

For a machine-readable CI artifact, replace `--json` with
`--json-file seo-release.json`. The target filename must not already exist
and must be outside the build tree. The command writes UTF-8 directly, does
not modify the build or sitemap and never uploads anything.

For CI services accepting JUnit test reports, use `--junit-file seo-release.xml`
instead of `--json`. To save **both** the complete JSON evidence and the JUnit
test results in one checked build, pass `--json-file seo-release.json` and
`--junit-file seo-release.xml` together. Both paths must be distinct, absent,
and outside the build tree; known invalid destinations are rejected before
either report is written. These create-only files preserve exit 0/1/2, and
stdout stays empty. A filesystem failure during writing can still leave a
partial artifact, so consumers must check the exit code before using reports.
The JUnit suite has one passing testcase when both gates pass or one failed
testcase per blocking HTML/sitemap finding.
See [offline release JUnit usage](../../../docs/SEO-RELEASE-JUNIT.md).

To give reviewers a compact human-readable report, add
`--markdown-file seo-release.md` alongside either or both artifact flags.
The Markdown summary includes HTML and sitemap status, bounded actionable
blocking findings, and a reminder that it checks **local artifacts**, not
public indexing. It escapes untrusted paths, URLs and issue text so build
content cannot inject Markdown/HTML into the summary. As with JSON/JUnit,
the Markdown path must be new and outside the build; **all three paths**
must differ. No Markdown report is written for input errors (exit 2).
For GitHub Actions, a separate authorized workflow step can append this
artifact to `$GITHUB_STEP_SUMMARY` after running the verifier. Do not point
`--markdown-file` directly at an existing step-summary file: it is create-only.
Always preserve the release check's exit status; producing a readable report
does not itself allow a failed gate to pass or publish a deployment.

To see deliberate failures without editing this snapshot, run
`uv run pytest tests/integration/test_release_gate_demo.py -q`. Those
acceptance tests copy the compiled build and introduce a pricing-page
`noindex`, then independently omit the pricing URL from a copied sitemap.
Both cause exit 1 for the correct failing subsystem, while the other passes.
The tests also deny outbound socket connections.

The `fixtures.invalid` URLs are *reviewed example assertions*, not reachable
sites; the CLI never fetches them. This demo does not validate production
rewrites, HTTP headers, search indexing, deployment behavior or customer
adoption. For your own project, compile the site, review actual public
routes and SEO policy, commit a manifest once, supply all local sitemap
shards explicitly and run the same gate on every subsequent build.
