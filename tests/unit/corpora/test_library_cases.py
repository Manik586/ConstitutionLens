"""User corpora recognise library judgments they contain and flag named cases they lack."""
from constitutional_evidence_rag.corpora.service import CorpusService

from corpora_fixtures import pdf_bytes

FILLER = ("This memorandum records the facts of a tenancy dispute between a landlord and a tenant over unpaid rent "
          "and repairs to the premises. ") * 10  # pushes later text past the title area (first 800 characters)


def build(service: CorpusService, corpus_id: str, filename: str, pages: list[str]):
    service.create_corpus(corpus_id)
    service.add_documents(corpus_id, [(filename, pdf_bytes(pages))])
    service.build_corpus(corpus_id)


def test_a_case_that_is_only_cited_is_flagged_as_not_in_this_corpus(service, curated):
    build(service, "briefs", "memo.pdf", [FILLER, "Counsel relied on Maneka Gandhi v. Union of India and argued that "
                                                  "any procedure depriving personal liberty must be fair and reasonable."])
    answer = service.query_corpus("briefs", "What did Maneka Gandhi argue?")
    assert answer.status.value == "insufficient_evidence"
    assert any("Maneka Gandhi v. Union of India is not in this corpus" in n for n in answer.notices)


def test_a_misspelled_missing_case_is_flagged_and_the_correction_reported(service, curated):
    build(service, "briefs", "memo.pdf", [FILLER, "The decision in Maneka Gandhi was cited for fairness of procedure."])
    answer = service.query_corpus("briefs", "what maenka gandhi argued for?")
    assert answer.status.value == "insufficient_evidence"
    assert any(n.startswith("Spelling: searched for 'maneka' instead of 'maenka'") for n in answer.notices)
    assert any("is not in this corpus" in n for n in answer.notices)


def test_an_uploaded_copy_of_a_library_judgment_is_recognised_by_its_title_page(service, curated):
    build(service, "my-cases", "scan_0042.pdf", [
        "MANEKA GANDHI v. UNION OF INDIA. Judgment of the Supreme Court on the impounding of a passport.",
        "The procedure prescribed by law for depriving a person of personal liberty must be fair, just and reasonable."])
    answer = service.query_corpus("my-cases", "What did Maneka Gandhi say about the procedure for depriving liberty?")
    assert not any("is not in this corpus" in n for n in answer.notices)
    assert answer.status.value in ("answered", "partial")


def test_the_curated_corpus_is_unaffected(service, curated):
    answer = service.query_corpus("constitutional-core", "What did Maneka Gandhi say about Article 21?")
    assert not any("is not in this corpus" in n for n in answer.notices)
