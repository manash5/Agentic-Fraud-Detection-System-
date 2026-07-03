FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG SERVICE=api-gateway
ARG PORT=8000

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
ENV SERVICE=${SERVICE}
ENV PORT=${PORT}

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY shared ./shared
COPY services ./services
COPY run_service.py .

EXPOSE ${PORT}
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; port = os.environ.get('PORT', '8000'); urllib.request.urlopen('http://127.0.0.1:%s/health' % port, timeout=3)"

CMD ["uv", "run", "--no-sync", "python", "run_service.py"]
