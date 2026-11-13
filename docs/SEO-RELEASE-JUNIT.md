# Offline SEO release gate: JUnit artifact

Searcharis can check a **reviewed local HTML manifest** and explicitly supplied **local XML sitemap URL sets** in one command, without fetching contract URLs, deploying anything, or calling paid services.

```sh
uv run python -m searcharis.release_cli \
  manifest.json dist sitemap.xml \
  --require-full-coverage \
  --require-indexability-coverage \
  --junit-file seo-release.xml
```

`--junit-file PATH` creates one JUnit XML test suite directly as an ASCII-compatible UTF-8 file for CI consumers such as GitLab/Jenkins. It prints no report to stdout and refuses to overwrite a file or write inside the build directory. Supply a fresh output path on each run. `--junit` prints the same XML suite to stdout; it is mutually exclusive with `--junit-file`, `--json`, and `--json-file`.

Each blocking HTML contract issue, policy-gated unlisted built page, missing reviewed sitemap URL, included reviewed noindex URL, or explicitly gated noncanonical URL becomes a separate failed testcase. With no blocking findings, the suite contains one passing testcase. XML names and diagnostics are deterministic, bounded, and escaped. Optional gate flags have the same meaning and defaults as the regular combined release checker; unlisted files are nonblocking unless `--require-full-coverage` is enabled. The JUnit output does not independently change the evaluated policy.

Exit status is **0** for both checks passing, **1** for SEO policy failure, or **2** for invalid input/output. Input errors go to stderr; no JUnit artifact is emitted for an input error because it is not a valid test run. A missing artifact should therefore be treated as a CI configuration/input problem, not a pass. Existing JSON and human output remain available.

CI integration must first build the site and supply developer-reviewed routes, canonical URLs, expected indexability and local sitemap shards. This demo does not prove the public routing, HTTP response headers, search-engine indexing, actual GitLab/Jenkins ingestion, customer adoption, or revenue. Keep deployments and protected workflows under their independent approval gates.
