"""Answer payloads shaped exactly like common/answer.py::Answer (model_dump(mode="json"))."""
from __future__ import annotations

DISCLAIMER = ("This is a legal research tool, not legal advice. Passages are quoted from retrieved sources; "
              "evidentiary support is not a determination of legal correctness or of whether a precedent is still good law.")


def chunk(evidence_no: int, *, corpus_id="briefs", document_id="DOC-1", page=3, text="Excerpt text."):
    return {
        "chunk_id": f"{document_id}@v1:{evidence_no:04d}", "corpus_id": corpus_id, "document_id": document_id,
        "document_version": 1, "source_type": "user_document", "source_url": "upload://doc.pdf", "chunk_index": evidence_no,
        "page_start": page, "page_end": page, "page_label_start": None, "page_label_end": None, "text": text,
        "token_count": 12, "chunking_method": "unstructured_page", "division": None, "part": None, "chapter": None,
        "article_number": None, "article_title": None, "clause_labels": [], "heading": None, "opinion_author": None,
        "case_name": None, "paragraph_number": None,
    }


def evidence(evidence_id: str, *, title="lease.pdf", page=3, text="Excerpt text.", corpus_id="briefs", role="user_document"):
    n = int(evidence_id[1:])
    return {
        "evidence_id": evidence_id, "rank": n, "role": role, "origin": "retrieval",
        "chunk": chunk(n, corpus_id=corpus_id, page=page, text=text), "document_title": title, "source_date": None,
        "hybrid_rank": n, "hybrid_score": 0.03, "bm25_rank": n, "bm25_score": 2.0, "dense_rank": n, "dense_score": 0.8,
        "boosts": {}, "final_score": 0.03, "term_coverage": 0.8, "reasons": ["origin: retrieval"],
    }


def citation(citation_id: int, ev: dict) -> dict:
    c = ev["chunk"]
    return {
        "citation_id": citation_id, "evidence_id": ev["evidence_id"], "chunk_id": c["chunk_id"], "document_id": c["document_id"],
        "document_version": 1, "source_type": "user_document", "title": ev["document_title"], "source_date": None,
        "article_number": None, "article_title": None, "opinion_author": None, "division": None,
        "page_start": c["page_start"], "page_end": c["page_end"], "page_label_start": None, "page_label_end": None,
        "source_url": c["source_url"],
    }


def generated_answer(query="When is the deposit refundable?"):
    """LLM answer (citation_style evidence_id) with a Phase 6 result: one kept, two removed."""
    e1 = evidence("E1", title="lease.pdf", page=3, text="The security deposit is refundable within thirty days of vacating.")
    e2 = evidence("E2", title="lease.pdf", page=4, text="Rent is payable on the fifth day of each month.")
    e3 = evidence("E3", title="addendum.pdf", page=1, text="The addendum concerns parking only.")
    return {
        "query": query, "query_type": "general", "status": "partial",
        "claims": [{"claim_id": "C1", "section": "answer",
                    "statement": "The security deposit is refundable within thirty days of vacating [E1].",
                    "quote": None, "citation_ids": [1], "basis": "explicit"},
                   {"claim_id": "C2", "section": "other_evidence",
                    "statement": "Rent is due on the fifth of each month [E2].",
                    "quote": None, "citation_ids": [2], "basis": "inference"}],
        "citations": [citation(1, e1), citation(2, e2)],
        "evidence": [e1, e2, e3],
        "notices": ["1 statement was removed by the evidence-support check."],
        "generator": "llm/openai_compatible/test-model/grounded-v1", "citation_style": "evidence_id",
        "validation": {"method": "lexical", "claims": [
            {"claim_id": "C1.1", "statement_id": "C1", "text": "The security deposit is refundable within thirty days of vacating.",
             "citation_ids": ["E1"], "supporting_ids": ["E1"], "claim_type": "propositional", "status": "supported",
             "score": 0.92, "coverage": 0.92, "semantic_score": None, "reason": "All key terms appear in E1.", "kept": True},
            {"claim_id": "C2.1", "statement_id": "C2", "text": "Rent is due on the fifth of each month.",
             "citation_ids": ["E2"], "supporting_ids": ["E2"], "claim_type": "propositional", "status": "supported",
             "score": 0.81, "coverage": 0.81, "semantic_score": None, "reason": "Key terms appear in E2.", "kept": True},
            {"claim_id": "C3.1", "statement_id": "C3", "text": "Late rent incurs a penalty of ten percent.",
             "citation_ids": ["E2"], "supporting_ids": [], "claim_type": "propositional", "status": "insufficient",
             "score": 0.2, "coverage": 0.2, "semantic_score": None, "reason": "Only 20% of the key terms appear in the cited evidence.", "kept": False},
            {"claim_id": "C4.1", "statement_id": "C4", "text": "The addendum allows pets.",
             "citation_ids": ["E3"], "supporting_ids": [], "claim_type": "propositional", "status": "contradicted",
             "score": 0.0, "coverage": 0.5, "semantic_score": None, "reason": "E3 says the addendum concerns parking only.", "kept": False},
        ]},
        "disclaimer": DISCLAIMER,
    }


def extractive_answer(query="What does the lease say about rent?"):
    """No-LLM answer: verbatim quotes, citation_style number, validation null."""
    e1 = evidence("E1", title="lease.pdf", page=4, text="Rent is payable on the fifth day of each month.")
    e2 = evidence("E2", title="lease.pdf", page=9, text="Interest accrues on late rent.")
    return {
        "query": query, "query_type": "general", "status": "answered",
        "claims": [{"claim_id": "C1", "section": "answer",
                    "statement": "lease.pdf, uploaded document: “Rent is payable on the fifth day of each month.” [1]",
                    "quote": "Rent is payable on the fifth day of each month.", "citation_ids": [1], "basis": None},
                   {"claim_id": "C2", "section": "answer",
                    "statement": "lease.pdf, uploaded document: “Interest accrues on late rent.” [2]",
                    "quote": "Interest accrues on late rent.", "citation_ids": [2], "basis": None}],
        "citations": [citation(1, e1), citation(2, e2)],
        "evidence": [e1, e2],
        "notices": ["Answers are generated from your uploaded documents only; they are not legal advice and not an authoritative statement of law."],
        "generator": "extractive-v1", "citation_style": "number", "validation": None, "disclaimer": DISCLAIMER,
    }


def empty_answer(query="Who won the 1983 cricket world cup?"):
    return {"query": query, "query_type": "general", "status": "insufficient_evidence", "claims": [], "citations": [],
            "evidence": [], "notices": ["No passage in this corpus matched the question."], "generator": "extractive-v1",
            "citation_style": "number", "validation": None, "disclaimer": DISCLAIMER}


def generation_failed_answer(query="Summarise the lease."):
    e1 = evidence("E1", title="lease.pdf", page=1, text="This lease is made between the parties.")
    return {"query": query, "query_type": "general", "status": "generation_failed", "claims": [], "citations": [],
            "evidence": [e1], "notices": ["The LLM call failed: no response within 60 s."], "generator": "llm/openai_compatible/test-model/grounded-v1",
            "citation_style": "evidence_id", "validation": None, "disclaimer": DISCLAIMER}
