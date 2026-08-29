FROM python:3.13-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PORT=8080

COPY . .

RUN if [ -f uv.lock ]; then \
      uv sync --frozen --no-dev; \
    else \
      uv sync --no-dev; \
    fi

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
CMD ["sh", "-c", "case \"${SERVICE_MODE:-ingress}\" in ingress) APP=searcharis.apps.ingress:app ;; worker) APP=searcharis.apps.worker:app ;; *) echo 'SERVICE_MODE must be ingress or worker' >&2; exit 2 ;; esac; exec uv run --no-sync uvicorn \"$APP\" --host 0.0.0.0 --port \"${PORT}\""]
