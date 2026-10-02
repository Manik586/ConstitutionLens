# V1 environment through Phase 3: PDF ingestion, chunking, BM25/dense retrieval.
# Reranking/LLM/UI dependencies are added in their own phases (D2).

FROM python:3.11-slim

WORKDIR /app

# build-essential is required for compiling PyMuPDF's native extensions
# on some platforms; removed from apt cache immediately to keep the image lean.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# hatchling needs README.md (pyproject `readme`) and the package source
# to exist at install time, so both are copied before `pip install`.
# CPU-only torch first (NFR-09: single developer machine); otherwise pip pulls the
# multi-GB CUDA build as a sentence-transformers dependency.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Embedding model weights are cached here; docker-compose mounts it so the model
# downloads once, not on every `docker compose run`.
ENV HF_HOME=/app/models

COPY configs/ configs/
COPY scripts/ scripts/

# No long-running application yet. Run the pipeline explicitly:
#   docker compose run --rm app python scripts/ingest_corpus.py
#   docker compose run --rm app python scripts/chunk_corpus.py
#   docker compose run --rm app python scripts/build_indexes.py
#   docker compose run --rm app python scripts/query.py "What does Article 21 provide?"
CMD ["python", "-c", "print('Constitutional Evidence RAG ready. Run: ingest_corpus.py -> chunk_corpus.py -> build_indexes.py -> query.py')"]
