# Integrating the offline SEO contract checker into CI

This guide starts with **built HTML** and then shows how to add reviewed local
sitemap and robots.txt checks to the same release gate. None of these checks is
a production crawl or an indexing guarantee. Use the existing Searcharis hosted
incident workflow separately when an external production audit is required.

Windows teams using PowerShell 5.1 or PowerShell 7 can start with the
[tested Windows CI quickstart](WINDOWS-SEO-CI.md). It includes native command
syntax, a checked-in passing and failing static-build fixture, portable
UTF-8 artifacts and exact CI exit-code propagation without Bash or output
redirection.

## 1. Build a known HTML artifact

For Astro, a standard static build produces files under `dist/`. Choose one
page deliberately rather than assuming a uniform path across languages or
trailing-slash configurations:

```sh
npm ci
npm run build
test -f dist/en/index.html
```

For other generators, use the corresponding output path. If your artifact
only exists after client-side JavaScript, the offline checker cannot
evaluate its rendered DOM.

## 2. Define an explicit contract

Store a JSON contract alongside each build fixture, with an absolute
public base URL. A minimal example:

```json
{
  "url": "https://example.com/en/",
  "title_present": true,
  "canonical": "https://example.com/en/",
  "indexable": true,
  "h1_min": 1,
  "h1_max": 1,
  "jsonld_types": ["WebPage"]
}
```

Only stated assertions are checked. In particular, `indexable` observes
HTML robots meta directives; it cannot inspect HTTP `X-Robots-Tag`,
robots.txt, or real search-engine indexing.

## 3. Check the artifact before deploying

From an environment with this Searcharis Python project installed:

```sh
uv run python -m searcharis.contract_cli   path/to/expected.json   path/to/dist/en/index.html   --json
```

Exit 0 means contract matched; 1 means an assertion failed; 2 means
invalid input. Keep a failing result as a failing CI result.

## 4. Bounded adoption

Start with the highest-value home, pricing and locale landing pages.
Use separate contracts per page initially. For multiple built pages, a
versioned JSON manifest provides per-page findings and explicit coverage:

```sh
uv run python -m searcharis.site_cli \
  path/to/site-manifest.json path/to/dist --json
```

The manifest declares relative HTML artifact paths under the build root and
their inline SEO contracts. Unlisted HTML is visible in the JSON report but
does not fail CI unless `--require-full-coverage` is supplied. Start with
important routes before enforcing whole-site coverage. Cross-page checks are
available **only when opted in**, including `--check-hreflang-reciprocity`,
`--check-hreflang-clusters`, `--check-canonical-consistency` and
`--check-reviewed-navigation`. They compare reviewed, locally built pages;
they do not crawl deployed URLs. CI does not mutate the production site.

### One-pass CI artifacts for reviewed builds

After reviewing and committing your starter manifest, keep it fixed across
builds. For a current-build HTML gate, create a **new, empty** report directory
outside the build tree and run one verification:

```sh
mkdir -p seo-ci-reports
uv run python -m searcharis.site_cli seo-manifest.json dist \
  --require-full-coverage --report-dir seo-ci-reports --github-annotations
```

This creates `seo-site.json` (complete findings), `seo-site.xml` (JUnit),
`seo-site.md` (bounded reviewer summary) and `seo-site.sarif` (SARIF 2.1.0)
without rechecking the same build four times. The directory must already
exist; target files must not exist. Do not put reports under `dist/` or reuse
stale report filenames. No uploads or workflow changes happen automatically.

If you have also reviewed explicit indexability assertions and your static
generator writes a local sitemap index, use the combined release checker
instead of running independent HTML and sitemap gates:

```sh
mkdir -p seo-release-reports
uv run python -m searcharis.release_cli seo-manifest.json dist \
  --sitemap-index dist/sitemap-index.xml \
  --require-full-coverage --require-indexability-coverage \
  --check-canonical-consistency --report-dir seo-release-reports --github-annotations
```

This creates `seo-release.json`, `seo-release.xml`, `seo-release.md` and
`seo-release.sarif`. Use explicit local sitemap XML paths instead of
`--sitemap-index` when no index exists. A valid reviewed manifest and local
sitemap are prerequisites; do not infer indexability from the generated file
alone. [Run the checked-in offline release example](../examples/seo-contract/release-demo/README.md)
before adapting these paths to your own static build.

For either command, **preserve exit 0 (pass), 1 (policy mismatch), and 2
(invalid input/output)** as the CI job result. Arrange separate, authorized
CI artifact collection even when the policy gate exits 1; do not interpret
missing or partial artifacts after exit 2 as a passing verification. JUnit
and SARIF consumers must be configured separately, with their own provider
permissions and pinned dependencies. Report generation itself performs no
network requests and does not grant release/deployment authority.

## 5. Release checks

- Smoke-test regression-only CI with the checked-in two-page example (from the
  repository root, Python 3.11+ and `uv` installed):

  ```sh
  uv run python -m searcharis.build_compare examples/seo-contract/regression-demo/manifest.json examples/seo-contract/regression-demo/baseline examples/seo-contract/regression-demo/candidate-passing --require-full-coverage --json
  # Expected exit 0: no new issues, one inherited home-page description issue.
  uv run python -m searcharis.build_compare examples/seo-contract/regression-demo/manifest.json examples/seo-contract/regression-demo/baseline examples/seo-contract/regression-demo/candidate --require-full-coverage --json
  # Expected exit 1: new missing pricing-page title; inherited debt remains allowed.
  ```

  CI must propagate the second command's nonzero status, rather than ignoring
  or converting it to a passing job. After smoke testing, replace the fixture
  paths with independently produced, immutable baseline/candidate build roots
  and use a developer-reviewed manifest. Neither demo verifies live URLs.
- For single-page checks, test with a known healthy HTML page and deliberately missing canonical.
- Confirm CI fails on the broken fixture rather than reporting a pass.
- Run the full repository offline unit/integration suite.
- Do not add production credentials to fixtures, logs or CI artifacts.
- Keep Google Cloud deploys manual; this guide changes no deployment workflow.

Implementation and supported fields:
[SEO-CONTRACT-CI.md](SEO-CONTRACT-CI.md).

For independently saved baseline/candidate JSON reports, see the
[archived-report Markdown review guide](ARCHIVED-REPORT-CI.md) for a bounded
human-readable CI artifact without rebuilding or fetching URLs.

For reproducible Astro-style multilingual and Vite-style static multipage
examples, see the [offline generator fixtures](../examples/seo-contract/generator-fixtures/README.md).
The checked-in snapshots are illustrative; always test your own actual build.

## Optional Cloudflare Pages `_redirects` artifact drift check

For Cloudflare Pages projects, compare a source-controlled reviewed `_redirects`
file with the final build-root artifact before deployment:

```sh
uv run python -m searcharis.redirects_policy_cli \
  policies/pages-redirects.txt dist --json
```

The reviewed file must be outside `dist`. Exit 0 means the bytes match, exit 1
means the built file is missing or differs, and exit 2 means invalid input.
For GitLab/Jenkins or PowerShell 5.1 CI consumers, use the optional
`--json-file reports/pages-redirects.json` (after creating `reports/`) to
save a new, create-only UTF-8 artifact without shell redirection. It cannot
overwrite reports or write into the compiled build, and retains exit 0/1/2.
This does not evaluate redirect syntax, fetch URLs or verify live routing.
See the [offline redirects guide](OFFLINE-REDIRECTS-CI.md) for a tested local
pass/fail fixture and Pages Functions/Bulk Redirects limitations.

## Optional reviewed `robots.txt` release safety

Before publishing a static build, compare its root `robots.txt` against a
source-controlled production policy that a reviewer deliberately approved:

```sh
uv run python -m searcharis.robots_policy_cli policies/production-robots.txt dist --json
```

For a standalone GitHub Actions job, use `--github-annotations` to emit
escaped `::error` annotations for changed/missing policies or explicitly
required Sitemap declarations. Passing gates emit no annotations. Combine
with `--json-file reports/robots.json` to keep a create-only UTF-8 artifact;
annotations are emitted only after successful report creation. Do not combine
the annotation flag with JSON stdout. This checks only local build artifacts,
does not fetch URLs or infer source file/line locations, and does not upload
or deploy anything. Preserve process exit status 0/1/2 in the CI job.

For a single offline run that saves reviewer-friendly Markdown alongside
machine-readable JSON and JUnit XML, create an empty reports directory first
and use the opt-in report bundle:

```sh
mkdir -p reports
uv run python -m searcharis.robots_policy_cli \
  policies/production-robots.txt dist --report-dir reports
```

This writes create-only `seo-robots.json`, `seo-robots.xml` and
`seo-robots.md` inside `reports/`, including on a drift failure (exit 1).
Alternatively, `--markdown-file reports/robots.md` can be paired with
explicit `--json-file`, `--junit-file` and `--github-annotations` output.
The checker rejects an existing artifact, a report path inside `dist`, a
missing parent directory, and overlapping destinations before writing any
report. Unexpected interrupted writes attempt identity-checked cleanup of
only the current invocation's artifacts; cleanup is best-effort, not atomic.
CI must preserve the command exit code and explicitly collect artifacts or
append the Markdown to its step summary: no automatic upload occurs. These
are current robots.txt findings, not baseline-regression comparisons.

Run this after generating `dist`, alongside the existing HTML and sitemap
checks, and preserve its exit status in CI. Exit 0 means the bytes match, 1
means the built policy differs or is missing, and 2 means invalid input.
Alternatively, combine all three checks in one local release gate:

```sh
uv run python -m searcharis.release_cli manifest.json dist sitemap.xml \
  --require-full-coverage --reviewed-robots policies/production-robots.txt --json
```

When selected, robots.txt drift blocks the combined exit status even when
HTML and sitemap pass. Structured JSON adds a `robots` result; JUnit, Markdown,
SARIF and default human output also retain the blocking finding. Without this
opt-in flag, the existing two-check report format and behavior are unchanged.
The approved reference must be outside the immutable build directory. This
guards against accidentally shipping an unreviewed staging `Disallow: /` but
does **not** parse robots rules, fetch a site or prove that the deployed
server exposes these bytes. For a no-network passing/failing demonstration,
see [reviewed robots policy fixtures](../examples/seo-contract/robots-policy/README.md).

If your deployment relies on robots.txt for sitemap discovery, opt into a
second, explicit review assertion in the same release command:

```sh
uv run python -m searcharis.release_cli manifest.json dist sitemap.xml \
  --reviewed-robots policies/production-robots.txt \
  --require-robots-sitemap https://example.com/sitemap.xml --json
```

This rejects a reviewed policy missing the specified `Sitemap:` directive,
even when its bytes match the compiled `robots.txt` (exit 1). Directive names
are case-insensitive and URL values are matched exactly. Multiple directives,
UTF-8 BOMs and trailing comments are supported; commented-out lines do not
count. Invalid expected URLs and malformed policy encoding are input errors
(exit 2). The flag requires `--reviewed-robots`; both are opt-in. It does not
verify a deployed URL or fetch a sitemap, and a robots.txt Sitemap directive
is optional in Google's protocol.
