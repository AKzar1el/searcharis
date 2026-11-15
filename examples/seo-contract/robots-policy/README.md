# Reviewed robots.txt static-build guard

Use the reviewed source policy as an explicit release artifact contract. A
staging-only `Disallow: /` accidentally copied into a production build must
not silently pass as though it were the reviewed production `robots.txt`.

From the repository root:

```sh
uv run python -m searcharis.robots_policy_cli \
  examples/seo-contract/robots-policy/reviewed.txt \
  examples/seo-contract/robots-policy/passing --json
# exit 0: byte-identical reviewed robots.txt

uv run python -m searcharis.robots_policy_cli \
  examples/seo-contract/robots-policy/reviewed.txt \
  examples/seo-contract/robots-policy/blocked --json
# exit 1: unreviewed robots.txt drift
```

For your site, commit a deliberately reviewed `robots.txt` reference outside
the build directory. After building, run the same command against the compiled
build root (`dist`, for example) and preserve its nonzero exit code in CI.
Missing built `robots.txt` is a release mismatch (exit 1); malformed inputs,
oversized policies or symlink aliases are input errors (exit 2). `--json`
prints only the file name, reason and SHA-256 digests, never the policy body.
The built filename must be exactly lowercase `robots.txt`, including on
case-insensitive Windows development filesystems.

This is **exact-byte drift detection**, not a robots.txt syntax or crawler
semantics validator. Googlebot uses user-agent selection and longest matching
Allow/Disallow precedence; do not infer its behavior from a simple substring
search. Matching local bytes cannot establish what production serves, whether
Googlebot can crawl URLs, or whether content is indexed. The check never
fetches URLs and does not deploy or edit the build.

See [Google's robots.txt interpretation](https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec)
and [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) when reviewing the
intended policy.
