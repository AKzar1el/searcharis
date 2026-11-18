# Offline Cloudflare Pages `_redirects` build guard

Cloudflare Pages reads a `_redirects` file from a project's **final static
asset directory**. A build pipeline can silently omit it or ship a changed
redirect table, including an accidental change from a permanent (301) to a
temporary (302) redirect. Compare a reviewed, source-controlled policy with
the compiled artifact **before deployment**, without requesting any URLs.

## First-use check

From this repository's root with Python 3.11+ and `uv` installed:

```sh
uv run python -m searcharis.redirects_policy_cli \
  examples/seo-contract/redirects-policy/reviewed.txt \
  examples/seo-contract/redirects-policy/passing --json
# Exit 0: approved bytes match the final build.

uv run python -m searcharis.redirects_policy_cli \
  examples/seo-contract/redirects-policy/reviewed.txt \
  examples/seo-contract/redirects-policy/blocked --json
# Exit 1: the compiled file differs from the approved reference.
```

The JSON result includes `status`, `artifact`, `reason`,
`expected_sha256` and `observed_sha256`. No rule bodies, URLs, or file paths
are included in successful or mismatch reports. Plain text is the default;
`--json` prints exactly one machine-readable object for success, mismatch or
invalid input.

For a **portable CI artifact** (including native Windows PowerShell 5.1, whose
stdout redirection can use UTF-16), create an existing report directory first
and use the separate opt-in output mode:

```sh
mkdir -p reports
uv run python -m searcharis.redirects_policy_cli \
  examples/seo-contract/redirects-policy/reviewed.txt \
  examples/seo-contract/redirects-policy/blocked \
  --json-file reports/pages-redirects.json
# Exit 1; a UTF-8 JSON report is retained for inspection.
```

In PowerShell 5.1 or 7, the equivalent commands use native syntax:

```powershell
New-Item -ItemType Directory -Path reports -Force | Out-Null
uv run python -m searcharis.redirects_policy_cli examples/seo-contract/redirects-policy/reviewed.txt examples/seo-contract/redirects-policy/blocked --json-file reports/pages-redirects.json
$LASTEXITCODE  # 1 means the reviewed bytes differ
```

`--json-file` and `--json` are mutually exclusive. The file mode emits no
JSON to stdout, creates a new report exclusively (never overwrites), requires
an existing parent and refuses destinations inside the build. Successful or
drifted comparisons produce the same JSON fields as `--json`. Invalid input
or report-write errors exit 2 without a successful report; interrupted partial
writes receive best-effort identity-checked cleanup, not an atomic guarantee.
Preserve exit codes in your CI shell. This command does not upload artifacts.

## Integrate into a build

1. Have a reviewer approve and commit a reference `_redirects` file **outside**
   the generated output tree, such as `policies/pages-redirects.txt`.
2. Build the site normally (for example, `npm run build`) and confirm the
   project's final Cloudflare Pages output directory (often `dist/`).
3. Run the check with the approved reference and the **final** output root:

   ```sh
   uv run python -m searcharis.redirects_policy_cli \
     policies/pages-redirects.txt dist --json
   ```

4. Preserve the process exit code in the CI job. Exit **0** means the exact
   bytes matched; **1** means the build-root `_redirects` is missing or
   changed; **2** means a missing/unsafe input, unreadable artifact, or other
   input failure. Neither a mismatch nor an input failure should become a
   green release gate. Inspect a diff before changing the reviewed reference.

This is an intentionally **byte-exact** review: whitespace, comments, line
endings, rule ordering and status codes are all material. The reviewed source
must be a readable file outside the build root. The compiled root must exist.
The checker will not follow a symlink named `_redirects` inside the build,
requires that exact case, and bounds input reads. It does not write to the
build, fetch a URL, upload a report, modify CI configuration, or deploy.

## Scope and hosting caveats

The comparison **does not parse, validate, execute or simulate** Cloudflare's
redirect language; matching bytes do not mean the rules are syntactically
valid or applied to live requests. In particular, Cloudflare Pages Functions
routes do not use `_redirects` rules, and Bulk Redirects can take precedence.
Hosting defaults, caching and custom-domain configuration are also outside
this check. See [Cloudflare's current Pages redirects documentation](https://developers.cloudflare.com/pages/configuration/redirects/)
for limits and routing details.

The checked-in [passing/failing examples](../examples/seo-contract/redirects-policy/README.md)
demonstrate behavior only. Before using it as a release gate, test your own
compiler's output path and ask a reviewer to approve the exact policy.
