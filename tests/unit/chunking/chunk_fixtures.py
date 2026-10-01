"""ParsedDocument builders for chunking tests (no PDFs needed).

The Constitution text follows the layout of the official bare-act PDF
(table of contents, Preamble, Parts, subheadings, "N. Title.—" article
headings, footnote insertion markers, omitted articles, Schedules). The
article wording is abridged. The judgments are synthetic and are not the
text of any real case.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from constitutional_evidence_rag.common.config import ChunkingSettings
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, DocumentMetadata, SourceType
from constitutional_evidence_rag.common.pages import ParsedDocument, ParsedPage
from constitutional_evidence_rag.common.provenance import provenance_from_document

SMALL = ChunkingSettings(target_tokens=60, max_tokens=80, overlap_tokens=15)


def make_document(
    pages: list[str],
    source_type: SourceType = SourceType.CONSTITUTIONAL_TEXT,
    *,
    corpus_id: str = CURATED_CORPUS_ID,
    document_id: str | None = None,
    version: int = 1,
    title: str = "Test document",
    labels: list[str | None] | None = None,
) -> tuple[ParsedDocument, DocumentMetadata]:
    prefix = {SourceType.CONSTITUTIONAL_TEXT: "CONST", SourceType.JUDGMENT: "JUDG", SourceType.AMENDMENT: "AMEND"}
    metadata = DocumentMetadata(
        document_id=document_id or f"{prefix[source_type]}-000000000001",
        corpus_id=corpus_id,
        source_type=source_type,
        title=title,
        source_url="https://example.org/doc",
        document_version=version,
        source_date=date(2026, 1, 1),
        ingestion_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    parsed_pages = [
        ParsedPage(
            provenance=provenance_from_document(metadata, page_number=n),
            text=text,
            is_empty=not text.strip(),
            page_label=(labels[n - 1] if labels else None),
        )
        for n, text in enumerate(pages, start=1)
    ]
    document = ParsedDocument(
        corpus_id=corpus_id, document_id=metadata.document_id, document_version=version, source_type=source_type,
        source_file="x.pdf", file_sha256="0" * 64, page_count=len(pages), pages=parsed_pages,
    )
    return document, metadata


CONSTITUTION = [
    # p1: title + table of contents (looks like articles, but has no ".—")
    "THE CONSTITUTION OF INDIA\nARRANGEMENT OF ARTICLES\nPREAMBLE\nPART I\nTHE UNION AND ITS TERRITORY\n"
    "1. Name and territory of the Union.\n2. Admission or establishment of new States.\n"
    "PART III\nFUNDAMENTAL RIGHTS\n14. Equality before law.\n21. Protection of life and personal liberty.",
    # p2: Preamble, Part I
    "PREAMBLE\nWE, THE PEOPLE OF INDIA, having solemnly resolved to constitute India into a\n"
    "SOVEREIGN SOCIALIST SECULAR DEMOCRATIC REPUBLIC and to secure to all its citizens:\n"
    "IN OUR CONSTITUENT ASSEMBLY do HEREBY ADOPT, ENACT AND GIVE TO OURSELVES THIS CONSTITUTION.\n"
    "PART I\nTHE UNION AND ITS TERRITORY\n"
    "1. Name and territory of the Union.—(1) India, that is Bharat, shall be a Union of States.\n"
    "(2) The States and the territories thereof shall be as specified in the First Schedule.\n"
    "2. Admission or establishment of new States.—Parliament may by law admit into the Union, or\n"
    "establish, new States on such terms and conditions as it thinks fit.",
    # p3: Part III with subheadings
    "PART III\nFUNDAMENTAL RIGHTS\nGeneral\n"
    "12. Definition.—In this Part, unless the context otherwise requires, \"the State\" includes the\n"
    "Government and Parliament of India.\n"
    "Right to Equality\n"
    "14. Equality before law.—The State shall not deny to any person equality before the law or the\n"
    "equal protection of the laws within the territory of India.",
    # p4: wrapped heading (19), footnote-marked insertion (21A)
    "Right to Freedom\n"
    "19. Protection of certain rights regarding freedom of speech,\n"
    "etc.—(1) All citizens shall have the right—\n(a) to freedom of speech and expression;\n"
    "(b) to assemble peaceably and without arms;\n"
    "(2) Nothing in sub-clause (a) of clause (1) shall affect the operation of any existing law.\n"
    "21. Protection of life and personal liberty.—No person shall be deprived of his life or personal\n"
    "liberty except according to procedure established by law.\n"
    "1[21A. Right to education.—The State shall provide free and compulsory education to all\n"
    "children of the age of six to fourteen years.]",
    # p5: omitted article, Part V with a Chapter and subheading
    "31. [Compulsory acquisition of property.] Omitted by the Constitution (Forty-fourth Amendment)\n"
    "Act, 1978, s. 6.\n"
    "PART V\nTHE UNION\nCHAPTER I.—THE EXECUTIVE\nThe President and Vice-President\n"
    "52. The President of India.—There shall be a President of India.",
    # p6: a Schedule whose numbered entries look like article headings
    "FIRST SCHEDULE\n[Articles 1 and 4]\nI. THE STATES\n"
    "1. Andhra Pradesh.—The territories specified in the Andhra Pradesh Act.\n"
    "2. Assam.—The territories which immediately before the commencement of this Constitution.",
]
CONSTITUTION_ARTICLES = ["1", "2", "12", "14", "19", "21", "21A", "31", "52"]

SCR_JUDGMENT = [
    # p1: reporter's front matter
    "EXAMPLE AND ORS.\nv.\nSTATE OF EXAMPLE AND ORS.\nAUGUST 13, 1997\n"
    "Constitution of India-Article 21-Right to life.\n"
    "HELD : 1. Each incident results in violation of the rights under Article 21.\n"
    "Mr. A. Counsel for the petitioners.",
    # p2: delimiter + opinion 1
    "The Judgment of the Court was delivered by\n"
    "VERMA, CJI. This Writ Petition has been filed for the enforcement of rights.\n"
    "The immediate cause for the filing of this writ petition is an incident.",
    # p3: opinion 2
    "The petitions are disposed of accordingly.\n"
    "AHMADI, CJ. I have had the benefit of reading the judgment of my learned brother.\n"
    "I agree with the conclusions.",
]

MODERN_JUDGMENT = [
    "REPORTABLE\nIN THE SUPREME COURT OF INDIA\nCIVIL APPELLATE JURISDICTION\nCivil Appeal No. 1 of 2020\n"
    "Petitioner v. Union of India\nJ U D G M E N T\n"
    "A. Facts\nThe appellant challenged the notification before the High Court.\n",
    "B. Issues for consideration\nWhether the notification violates Article 14.\n"
    "Analysis\nSubmissions of the appellant\nThe appellant submits that the notification is arbitrary.\n",
    "Conclusion\nThe appeal is allowed. Order accordingly.",
]
