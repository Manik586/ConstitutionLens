from constitutional_evidence_rag.common.config import load_settings
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.generation.pipeline import AnswerPipeline
from constitutional_evidence_rag.validation.support import SupportValidator

settings = load_settings()
pipeline = AnswerPipeline.load(processed_root=settings.paths.data_processed_dir,
                               indexes_root=settings.paths.indexes_dir,
                               corpus_id=CURATED_CORPUS_ID, settings=settings)

query = "What does Article 21 provide?"
evidence = pipeline.select(pipeline.analyze(query))
print("Evidence retrieved:")
for e in evidence.items:
    print(f"  {e.evidence_id}  {e.document_title[:55]}  Art.{e.chunk.article_number or '-'}  "
          f"PDF pp. {e.chunk.page_start}-{e.chunk.page_end}")

by_id = evidence.by_id()
print("\nE2 text:\n", " ".join(by_id["E2"].chunk.text.split())[:700])

claims = [
    ("Article 21 protects life and personal liberty.", ["E1"]),
    ("Article 21 provides that personal liberty can be taken away without legal procedure.", ["E1"]),
    ("Maneka Gandhi held that the procedure must be fair and reasonable.", ["E1"]),
    ("Article 21 guarantees free legal aid to every prisoner.", ["E1"]),
    ("The right to life under Article 21 is absolute.", ["E1"]),
    ("This contention of the petitioner raises a question as to the true interpretation of Article 21.", ["E2"]),
    ("The right to personal liberty includes the right to go abroad.", ["E2"]),
    ("The right to travel abroad was expressly excluded from personal liberty.", ["E2"]),
]

# This forces the embedding model to load now (it's otherwise loaded lazily on first dense query).
pipeline.retriever.dense(query, top_n=1)
embedder = pipeline.retriever._embedder


# Two independent copies — changing one never affects the other
lexical_settings = settings.evidence_validation.model_copy(update={"method": "lexical"})
semantic_settings = settings.evidence_validation.model_copy(update={"method": "semantic"})

validator_lexical = SupportValidator(lexical_settings)
validator_semantic = SupportValidator(semantic_settings, embedder_factory=lambda: pipeline.retriever._embedder)

print("--- LEXICAL (default) ---")
for text, ids in claims:
    r = validator_lexical.validate(text, ids, by_id)
    print(f"{r.status.value.upper():13} score {r.score:.2f}  {text}")
    print(f"    {r.reason}\n")

print("--- SEMANTIC ---")
for text, ids in claims:
    r = validator_semantic.validate(text, ids, by_id)
    print(f"{r.status.value.upper():13} score {r.score:.2f}  {text}")
    print(f"    {r.reason}\n")