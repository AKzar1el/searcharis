# Offline Cloudflare Pages `_redirects` drift guard

From the repository root (Python 3.11+ and `uv` installed), run:

```sh
uv run python -m searcharis.redirects_policy_cli \
  examples/seo-contract/redirects-policy/reviewed.txt \
  examples/seo-contract/redirects-policy/passing --json
# exit 0: reviewed redirect bytes match the static build

uv run python -m searcharis.redirects_policy_cli \
  examples/seo-contract/redirects-policy/reviewed.txt \
  examples/seo-contract/redirects-policy/blocked --json
# exit 1: a formerly permanent redirect now uses 302 in the built artifact
```

This comparison reads only local files. It detects missing or changed build-root
`_redirects` bytes; it does **not** interpret wildcard/placeholder rules,
resolve redirect chains, verify the hosting service, or fetch public URLs.
The sample rules are illustrative, not a recommendation for your live routes.

Review a separate source-controlled `_redirects` reference outside your
immutable build, then compare it to `dist/_redirects` after each static build.
Keep exit 1 as a CI failure, and exit 2 as an invalid-input failure. A passing
comparison is not proof that Cloudflare will serve the rules: Pages Functions,
Bulk Redirects and other routing can take precedence.

See [the integration guide](../../../docs/OFFLINE-REDIRECTS-CI.md).
