# One-command offline Cloudflare Pages static policy gate

Use this **opt-in** predeployment check when a static Cloudflare Pages build must
retain both a reviewed `_redirects` file and a reviewed `_headers` file.
Running one command prevents a CI script from losing the first check's failure
exit code while running the second. It does not fetch URLs or call Cloudflare.

New to this check? Run the [checked-in synthetic quickstart](../examples/pages-policy/README.md)
first. It verifies the actual offline CLI, a passing local static build,
portable report artifacts, and isolated drift/missing-input regressions without
requiring a Cloudflare account or modifying your site's build.

## CI use

First commit **separate, reviewer-approved** reference files outside the output
directory. Build your site, identify its final static asset root (for example,
`dist`), and invoke:

```sh
uv run python -m searcharis.pages_policy_cli \
  policies/approved-redirects.txt policies/approved-headers.txt dist
```

In native Windows PowerShell 5.1/7, the same invocation is a single line:

```powershell
uv run python -m searcharis.pages_policy_cli policies/approved-redirects.txt policies/approved-headers.txt dist --json-file reports/pages.json --junit-file reports/pages.xml --github-annotations
$code = $LASTEXITCODE
exit $code
```

Create `reports/` before invoking the command. `--json-file PATH` writes one
**new**, portable UTF-8 report outside `dist`, without overwriting an existing
file. Optional `--junit-file PATH` creates a JUnit XML report in the same
verification pass, with separate `_redirects` and `_headers` testcases and
failure details. GitLab, Jenkins and other JUnit-aware CI systems can ingest
the XML while preserving this command's blocking exit status. Either artifact
can be requested alone or both together. All destinations must be distinct,
outside the build, under existing parent directories and checked before the
first write. Interrupted writes receive identity-checked, best-effort cleanup,
not transactional multi-file atomicity.

For a reviewer-friendly **one-flag report bundle**, create an empty reports
directory and run the same offline verification once:

```sh
mkdir -p reports
uv run python -m searcharis.pages_policy_cli \
  policies/approved-redirects.txt policies/approved-headers.txt dist \
  --report-dir reports
```

This creates `seo-pages.json`, `seo-pages.xml` and `seo-pages.md` under
`reports/`. The Markdown summary gives a bounded PASS/FAIL for each static
artifact, with short reviewed/observed hashes and an actionable failure hint.
Alternatively, `--markdown-file reports/pages.md` can accompany explicit
`--json-file` and `--junit-file` destinations. `--report-dir` cannot be
combined with those explicit file options or `--json` stdout, but it can
be used with `--github-annotations`. All three outputs are preflighted as
create-only before the first write; a pre-existing report, an absent parent
directory or any destination inside the build blocks the entire bundle with
exit 2. If a later write fails, cleanup of already created files is
best-effort and identity-checked rather than atomic.

In GitHub Actions, upload the artifacts explicitly and, if desired, append
`seo-pages.md` to `GITHUB_STEP_SUMMARY` after retaining the check's original
exit code. Searcharis never uploads reports or silently changes a failing CI
exit to success. On PowerShell, create the directory with
`New-Item -ItemType Directory -Path reports` and capture `$LASTEXITCODE`
immediately after the checker.

On Unix, preserve the exit status with the shell's standard command handling;
do not append `|| true`. Use `--json` instead when consuming JSON from stdout;
stdout JSON cannot be combined with either file report mode.

`--github-annotations` is optional for GitHub Actions jobs. On exit 1 it
prints one GitHub `::error` annotation for each missing or changed artifact,
so reviewers can identify `_redirects` and `_headers` failures separately.
Exit 0 prints no annotations. With file reports, annotations are printed only
after every requested report is successfully created, never when a write fails. Do not
combine annotations with `--json` stdout. The messages refer to compiled
artifacts, not invented source-line locations; other CI systems can omit the
flag and consume JSON or JUnit instead.

For a drifting compiled artifact, add `--locate-drift` to identify its **first
differing byte** relative to the reviewed reference. This opt-in local-only
diagnostic adds a zero-based byte offset and one-based LF-delimited line/byte
column to JSON; the same position appears in human, Markdown, JUnit and
GitHub error text. It never prints rule contents and does not forge a GitHub
source-file location. A missing artifact has no position. This diagnoses
byte differences (including whitespace and line endings), not Pages behavior.

| Exit | Meaning | CI action |
| --- | --- | --- |
| 0 | Both compiled files match the reviewed references byte-for-byte | Pass |
| 1 | At least one compiled file is missing or differs | Block and review |
| 2 | An input, destination, or file-read error occurred | Block and repair |

On exit 0 or 1, one report contains a `checks` array with both artifacts,
individual `pass`/`fail` decisions and SHA-256 digests. On input error, the
stdout `--json` form includes the completed checks and separately identified
`errors` instead of hiding the other result. File report modes do not create
reports on invalid checker input (exit 2); never treat an older report as
current. Destination or write failures may leave partial files if cleanup
cannot safely identify them; this is **not atomic**.
Invalid inputs still exit 2 with stderr diagnostics, without success or drift
annotations. In the default human-readable mode, any other artifact that was
successfully checked is also reported on stderr as a **Partial result** (PASS
or FAIL). It does not change the overall exit 2, but prevents a separately
detected drift from disappearing behind the invalid-input message.

Byte parity intentionally treats whitespace, rule order and line endings as
reviewed content. It is not a syntax checker, runtime redirect emulator,
live-response audit or SEO ranking measurement. Cloudflare Pages `_headers`
applies to static assets, not Pages Functions/SSR/`_worker.js` responses;
`_redirects` also has route-precedence limitations. For standalone checks
and fuller limitations, see [headers](OFFLINE-HEADERS-CI.md) and
[redirects](OFFLINE-REDIRECTS-CI.md).

### Optional Pages policy size limits

Exact byte parity can pass even if a reviewed rule is too long for Cloudflare
Pages to accept. Add `--check-policy-limits` to the combined command to
fail CI when a matching, reviewed static policy violates basic published limits:
1,000 characters per `_redirects` declaration; 2,000 static and 100 dynamic
redirect rules (2,100 total); 2,000 characters per `_headers` line; and 100
top-level header rule blocks. The default remains byte-only. Opt-in failures retain exit
1 and identify the artifact and offending line or rule count in JSON, JUnit,
Markdown, human output and GitHub annotations.

This checks **basic bounds only**; redirect rules are classified as dynamic if
the source token contains a wildcard or `:[A-Za-z]...` placeholder. It does not
validate full redirect/header syntax, proxying or status codes, emulate
precedence or claim that Pages will accept the policy.
The check reads only local UTF-8 text and never fetches URLs.
See Cloudflare's [redirect limits](https://developers.cloudflare.com/pages/configuration/redirects/)
and [header limits](https://developers.cloudflare.com/pages/configuration/headers/).

This command makes no deployments, paid API calls or production network
requests. Customer adoption and false-positive rates have not been measured.
