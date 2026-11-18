# Offline Cloudflare Pages policy CI: first run

This checked-in **synthetic** static build lets you verify the Pages policy
gate before using a real site. The checker makes no outbound requests;
no Cloudflare account, deployment, customer data or production credentials
are needed. Initial `uv` dependency installation may use a package registry.

- `reviewed/_redirects` and `reviewed/_headers` are the approved references.
- `site-root/` stands in for the output of a static framework build, such
  as Astro's `dist/`. It includes matching `_redirects` and `_headers`
  plus a minimal HTML page.
- The illustrative redirect sends `/old-page` to `/new-page`; the illustrative
  header applies `X-Robots-Tag: noindex` **only** to `/private/*`.
  Neither file is a recommendation to deploy those rules to your site.

From the **repository root**, run:

```sh
uv run python -m searcharis.pages_policy_cli \
  examples/pages-policy/reviewed/_redirects \
  examples/pages-policy/reviewed/_headers \
  examples/pages-policy/site-root --json
```

Expected: exit **0**, one JSON object with `status: pass` and two passing
checks. Exit **1** means reviewed policy bytes have drifted or a compiled
artifact is missing; exit **2** means invalid input or an unsafe output.
Do not suppress either failing exit code in your CI.

## Create all three reports from the same offline check

On Bash or POSIX shells, create a **new** temporary output directory:

```sh
reports="$(mktemp -d)"
uv run python -m searcharis.pages_policy_cli \
  examples/pages-policy/reviewed/_redirects \
  examples/pages-policy/reviewed/_headers \
  examples/pages-policy/site-root --report-dir "$reports"
status=$?
printf 'Pages policy exit code: %s\n' "$status"
exit "$status"
```

On native Windows PowerShell 5.1/7:

```powershell
$reports = Join-Path $env:TEMP ("searcharis-pages-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $reports -ErrorAction Stop | Out-Null
uv run python -m searcharis.pages_policy_cli examples/pages-policy/reviewed/_redirects examples/pages-policy/reviewed/_headers examples/pages-policy/site-root --report-dir $reports
$status = $LASTEXITCODE
Write-Output "Pages policy exit code: $status"
exit $status
```

Expect `seo-pages.json`, `seo-pages.xml` (two JUnit testcases) and
`seo-pages.md` in the new directory. Existing reports are never overwritten;
a second run against the same output names exits **2**. Preserve the checker
status when wiring this into a CI job.

## See a real regression without touching checked-in fixtures

Copy `site-root/` to a **separate scratch directory** and edit only the
copy's `_headers` or `_redirects`. Re-run the first command with the
scratch directory as its last argument. Expect **exit 1** and one failing
artifact in the JSON report; the other remains a pass. Deleting an artifact
from that scratch copy instead produces a `missing` failure. Invalid reviewed
paths return exit 2. The automated acceptance test
`tests/integration/test_pages_policy_quickstart.py` verifies all cases.

For your own project, approve your own static source files separately from
your actual **built** static asset directory; never compare a file against
itself or treat this synthetic fixture as proof of deployed redirect/header
behavior. Place Cloudflare's source `_redirects`/`_headers` in a static
asset directory such as `public/` so the build copies them to its root.

Cloudflare's [redirects](https://developers.cloudflare.com/pages/configuration/redirects/)
and [headers](https://developers.cloudflare.com/pages/configuration/headers/)
documentation explains the static-assets-only semantics and exclusions for
Pages Functions/SSR. This checker verifies byte parity, **not** whether
Cloudflare accepts a rule, applies it to deployed requests or changes rankings.

See [the complete Pages CI guide](../../docs/OFFLINE-PAGES-CI.md) for production
CI report and annotation options. A real-site trial requires separate approval.
