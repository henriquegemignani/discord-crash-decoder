FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS build
RUN pip install --no-cache-dir uv==0.12.13
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && .venv/bin/crash-decoder symbols validate

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home decoder
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 OMP_THREAD_LIMIT=1
USER decoder
WORKDIR /app
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD ["crash-decoder", "health"]
ENTRYPOINT ["crash-decoder"]
CMD ["bot"]

FROM build AS development
RUN uv sync --frozen --no-editable

FROM runtime AS test
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=development /app/.venv /app/.venv
COPY tests ./tests
COPY scripts ./scripts
COPY registry.json ./registry.json
COPY pyproject.toml ./pyproject.toml
USER decoder
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["-q", "-p", "no:cacheprovider"]

FROM runtime AS final
