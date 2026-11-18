# One-command offline Cloudflare Pages static policy gate

Use this **opt-in** predeployment check when a static Cloudflare Pages build must
retain both a reviewed `_redirects` file and a reviewed `_headers` file.
Running one command prevents a CI script from losing the first check's failure
exit code while running the second. It does not fetch URLs or call Cloudflare.

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
uv run python -m searcharis.pages_policy_cli policies/approved-redirects.txt policies/approved-headers.txt dist --json-file reports/pages.json
$code = $LASTEXITCODE
exit $code
```

Create `reports/` before invoking the command. `--json-file PATH` writes one
**new**, portable UTF-8 report outside `dist`, without overwriting an existing
file. On Unix, preserve the exit status with the shell's standard command
handling; do not append `|| true`. Use `--json` instead when consuming JSON
from stdout; the two output options are mutually exclusive.

| Exit | Meaning | CI action |
| --- | --- | --- |
| 0 | Both compiled files match the reviewed references byte-for-byte | Pass |
| 1 | At least one compiled file is missing or differs | Block and review |
| 2 | An input, destination, or file-read error occurred | Block and repair |

On exit 0 or 1, one report contains a `checks` array with both artifacts,
individual `pass`/`fail` decisions and SHA-256 digests. On input error, the
stdout `--json` form includes the completed checks and separately identified
`errors` instead of hiding the other result. The `--json-file` form does not
create a report on exit 2; never treat an older report as current. Partial-write
cleanup is identity-checked and best-effort, **not atomic**.

Byte parity intentionally treats whitespace, rule order and line endings as
reviewed content. It is not a syntax checker, runtime redirect emulator,
live-response audit or SEO ranking measurement. Cloudflare Pages `_headers`
applies to static assets, not Pages Functions/SSR/`_worker.js` responses;
`_redirects` also has route-precedence limitations. For standalone checks
and fuller limitations, see [headers](OFFLINE-HEADERS-CI.md) and
[redirects](OFFLINE-REDIRECTS-CI.md).

This command makes no deployments, paid API calls or production network
requests. Customer adoption and false-positive rates have not been measured.
