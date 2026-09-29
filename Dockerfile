# Phase 1 environment: Python runtime + PDF ingestion dependencies only.
# No retrieval/LLM/UI dependencies are installed at this stage.

FROM python:3.11-slim

WORKDIR /app

# build-essential is required for compiling PyMuPDF's native extensions
# on some platforms; removed from apt cache immediately to keep the image lean.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# hatchling needs README.md (pyproject `readme`) and the package source
# to exist at install time, so both are copied before `pip install`.
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e .

COPY configs/ configs/
COPY scripts/ scripts/

# Phase 1 has no long-running application. Run ingestion explicitly:
#   docker compose run --rm app python scripts/ingest_corpus.py
CMD ["python", "-c", "print('Constitutional Evidence RAG - Phase 1 environment ready. Run: python scripts/ingest_corpus.py')"]
