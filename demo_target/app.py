from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

_ALLOWED_VARIANTS = {"healthy", "broken"}
_TITLE = "Searcharis Demo Store - Search Safe"
_DESCRIPTION = (
    "A deterministic public demo storefront used to prove that Searcharis detects, reports, "
    "and independently verifies post-deployment search regressions."
)


def render_page(variant: str, base_url: str = "https://demo.example/") -> str:
    if variant not in _ALLOWED_VARIANTS:
        raise ValueError("TARGET_VARIANT must be either 'healthy' or 'broken'")
    title = f"<title>{_TITLE}</title>" if variant == "healthy" else ""
    canonical = base_url.rstrip("/") + "/"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {title}
  <meta name="description" content="{_DESCRIPTION}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:title" content="{_TITLE}">
  <meta property="og:image" content="{canonical}preview.png">
  <script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebSite","name":"Searcharis Demo Store","url":"{canonical}"}}</script>
</head>
<body>
  <main>
    <h1>Searcharis deployment regression demo</h1>
    <p>This stable page exists only to demonstrate detection and recovery verification.</p>
  </main>
</body>
</html>"""


def create_app(variant: str | None = None) -> FastAPI:
    selected = variant or os.getenv("TARGET_VARIANT", "healthy")
    if selected not in _ALLOWED_VARIANTS:
        raise ValueError("TARGET_VARIANT must be either 'healthy' or 'broken'")
    app = FastAPI(title="Searcharis Demo Target")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return HTMLResponse(render_page(selected, str(request.base_url)))

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "variant": selected}

    return app


app = create_app()
