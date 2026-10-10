# Constitutional Evidence RAG — API server + pipeline scripts (Phase 7; D29).
# One image for every deployment profile (SRS §40, NFR-11). The API has NO authentication:
# deploy it only where every user is trusted (SRS §40.3 `private` profile).

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CER_CONFIG=/app/configs/v1.yaml \
    CER_DATA_ROOT=/app \
    HF_HOME=/app/models \
    PORT=8000

WORKDIR /app

# build-essential is required for compiling PyMuPDF's native extensions on some platforms.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch first (NFR-09/NFR-12); otherwise pip pulls the multi-GB CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# hatchling needs README.md and the package source at install time.
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e ".[api]"

COPY configs/ configs/
COPY scripts/ scripts/

# Non-root runtime user; data, indexes and the model cache are the only writable paths.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p data/raw data/processed data/uploads indexes models \
    && chown -R app:app /app/data /app/indexes /app/models
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=4)" || exit 1

# The pipeline scripts still work, e.g.:
#   docker compose run --rm app python scripts/query.py "What does Article 21 provide?"
CMD ["sh", "-c", "exec uvicorn --factory constitutional_evidence_rag.api.app:create_app --host 0.0.0.0 --port ${PORT:-8000}"]
