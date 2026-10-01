import re

import pytest

from constitutional_evidence_rag.chunking.structure import Line, detect_sections, document_lines
from constitutional_evidence_rag.common.chunks import Division
from constitutional_evidence_rag.common.models import SourceType

from chunk_fixtures import CONSTITUTION, CONSTITUTION_ARTICLES, MODERN_JUDGMENT, SCR_JUDGMENT, make_document

C, J = SourceType.CONSTITUTIONAL_TEXT, SourceType.JUDGMENT


def sections_of(pages, source_type=C):
    document, _ = make_document(pages, source_type)
    return detect_sections(document_lines(document), source_type)


def by_article(sections):
    return {s.article_number: s for s in sections if s.article_number}


# ------------------------------------------------------------------ Constitution


def test_articles_detected_in_order_and_toc_is_not_mistaken_for_articles():
    sections = sections_of(CONSTITUTION)

    assert [s.article_number for s in sections if s.article_number] == CONSTITUTION_ARTICLES
    front = sections[0]
    assert front.division is Division.FRONT_MATTER and front.article_number is None
    assert "ARRANGEMENT OF ARTICLES" in [l.text for l in front.lines]


def test_every_article_section_starts_at_its_own_heading_and_contains_no_other():
    for section in (s for s in sections_of(CONSTITUTION) if s.article_number):
        headings = [l.text for l in section.lines
                    if re.sub(r"^\d{0,2}\[", "", l.text).startswith(f"{section.article_number}.")]
        assert len(headings) == 1, (section.article_number, [l.text for l in section.lines])


def test_parts_chapters_and_subheadings_are_attached():
    articles = by_article(sections_of(CONSTITUTION))

    assert articles["1"].part == "PART I — THE UNION AND ITS TERRITORY"
    assert articles["14"].part == "PART III — FUNDAMENTAL RIGHTS"
    assert (articles["12"].heading, articles["14"].heading, articles["19"].heading) == (
        "General", "Right to Equality", "Right to Freedom")
    assert articles["21"].heading == "Right to Freedom"  # subheading carries forward within its group
    assert articles["52"].part == "PART V — THE UNION" and articles["52"].chapter == "CHAPTER I.—THE EXECUTIVE"
    assert articles["52"].heading == "The President and Vice-President"


def test_part_and_subheading_lines_stay_with_the_article_they_introduce():
    articles = by_article(sections_of(CONSTITUTION))

    assert [l.text for l in articles["12"].lines[:4]] == ["PART III", "FUNDAMENTAL RIGHTS", "General",
                                                          articles["12"].lines[3].text]
    assert articles["14"].lines[0].text == "Right to Equality"


def test_article_titles_including_wrapped_footnoted_and_omitted():
    articles = by_article(sections_of(CONSTITUTION))

    assert articles["19"].article_title == "Protection of certain rights regarding freedom of speech, etc"
    assert articles["21A"].article_title == "Right to education"
    assert articles["31"].article_title == "Compulsory acquisition of property"


def test_top_level_clauses_are_recorded():
    articles = by_article(sections_of(CONSTITUTION))

    assert list(articles["1"].clause_starts.values()) == ["(1)", "(2)"]
    assert list(articles["19"].clause_starts.values()) == ["(1)", "(2)"]  # (a)/(b) are sub-clauses, not recorded
    assert articles["14"].clause_starts == {}


def test_preamble_is_its_own_body_section():
    sections = sections_of(CONSTITUTION)
    preamble = next(s for s in sections if s.heading == "Preamble")

    assert preamble.division is Division.BODY and preamble.article_number is None
    assert preamble.lines[1].text.startswith("WE, THE PEOPLE OF INDIA")


def test_schedule_entries_are_not_articles():
    sections = sections_of(CONSTITUTION)
    schedule = sections[-1]

    assert schedule.heading == "FIRST SCHEDULE" and schedule.article_number is None
    assert any(l.text.startswith("1. Andhra Pradesh.—") for l in schedule.lines)


def test_out_of_sequence_numbered_line_is_text_not_an_article():
    pages = ["PART III\nFUNDAMENTAL RIGHTS\n21. Protection of life.—No person shall be deprived of life.\n"
             "3. Something.—a numbered line inside the article text\n22. Protection against arrest.—No person."]
    sections = sections_of(pages)

    assert [s.article_number for s in sections if s.article_number] == ["21", "22"]
    assert any(l.text.startswith("3. Something") for l in by_article(sections)["21"].lines)


def test_footnote_marked_subheading_replaces_the_carried_one():
    pages = ["PART III\nFUNDAMENTAL RIGHTS\nRight to Equality\n14. Equality before law.—The State shall not deny.\n"
             "4[Saving of Certain Laws]\n31A. Saving of laws.—Notwithstanding anything contained in article 13."]
    articles = by_article(sections_of(pages))

    assert articles["31A"].heading == "Saving of Certain Laws"


def test_constitution_without_detectable_articles_falls_back():
    sections = sections_of(["Some text without article headings.\nAnother line of plain text."])

    assert len(sections) == 1 and not sections[0].structural and sections[0].article_number is None


# ------------------------------------------------------------------ judgments


def test_scr_judgment_front_matter_and_opinions():
    sections = sections_of(SCR_JUDGMENT, J)

    assert [(s.division, s.heading, s.opinion_author) for s in sections] == [
        (Division.FRONT_MATTER, None, None),
        (Division.FRONT_MATTER, "HELD", None),  # the reporter's headnote: labelled front matter, not opinion
        (Division.OPINION, None, "VERMA, CJI."),
        (Division.OPINION, None, "AHMADI, CJ."),  # not "AHMADI, CJ. I" from "CJ. I have had..."
    ]
    assert sections[2].lines[0].text == "The Judgment of the Court was delivered by"


def test_modern_judgment_headings_are_verbatim_and_title_lines_merge_forward():
    sections = sections_of(MODERN_JUDGMENT, J)

    assert [s.heading for s in sections] == [
        None, "A. Facts", "B. Issues for consideration", "Submissions of the appellant", "Conclusion"]
    assert sections[1].lines[0].text == "J U D G M E N T"  # bare title line joins the next section
    assert [l.text for l in sections[3].lines[:2]] == ["Analysis", "Submissions of the appellant"]
    assert sections[0].division is Division.FRONT_MATTER
    assert all(s.division is Division.OPINION for s in sections[1:])


def test_judgment_headings_are_not_mapped_to_a_section_type_taxonomy():
    # V1 records headings as printed; classifying facts/arguments/holding is V2 (FR-JS-01)
    sections = sections_of(MODERN_JUDGMENT, J)
    assert "section_type" not in vars(sections[1])


@pytest.mark.parametrize("line", [
    "order accordingly.",  # lowercase start: mid-sentence
    "Order accordingly.",  # mixed-case sentence ending in a full stop
    "Held, such incidents result in violation of gender equality",  # comma: a sentence
    "Facts of the case which were placed before the High Court were considered in detail at length",
    "Submissions -",
])
def test_heading_false_positives_are_rejected(line):
    pages = [f"The Judgment of the Court was delivered by\nVERMA, CJI. The first sentence ends here.\n{line}\nNext line."]
    sections = sections_of(pages, J)

    assert all(s.heading is None for s in sections)


def test_judgment_without_structural_signals_falls_back():
    sections = sections_of(["The appellant filed a petition.\nThe respondent replied to it."], J)

    assert len(sections) == 1 and not sections[0].structural and sections[0].division is None


def test_inconsistent_formatting_across_judgments_does_not_break_detection():
    # headings only (no opinion delimiter): sections exist, but no front matter/opinion split is invented
    sections = sections_of(["Background\nThe dispute arose in 2001.\nIssues\nWhether the levy is valid."], J)

    assert [s.heading for s in sections] == ["Background", "Issues"]
    assert all(s.division is None for s in sections)


def test_amendment_documents_use_the_unstructured_fallback():
    sections = sections_of(["2. Amendment of article 15.—In article 15 of the Constitution, after clause (4)."],
                           SourceType.AMENDMENT)

    assert len(sections) == 1 and not sections[0].structural


# ------------------------------------------------------------------ page furniture


def test_running_headers_page_numbers_and_margin_letters_are_removed():
    bodies = ["The petition was filed in 1992.", "Notice was issued to the State.", "Counsel were heard at length.",
              "The guidelines are laid down below."]
    pages = [f"{n}\nEXAMPLE v. STATE [VERMA, CJI.] {400 + n}\nA\n{body}\nPage {n} of 4" for n, body in enumerate(bodies, 1)]
    document, _ = make_document(pages, J)

    assert [l.text for l in document_lines(document)] == bodies


def test_ocr_variants_of_a_running_header_are_removed_even_mid_page():
    variants = ["EXAMPLE v. STATE [VERMA, CJI.]", "EXAMPLEv. STATE[VERMA, CTI.]", "EXAMPLE v. STATE [VERMA, OI.J",
                "EXAMPLE v. STATE [VERMA, CJI.]"]
    pages = [f"{v}\nFirst body line {i}.\nSecond body line {i}." for i, v in enumerate(variants[:3])]
    pages.append(f"First body line 3.\n{variants[3]}\nSecond body line 3.\nThird body line 3.")  # mid-page
    document, _ = make_document(pages, J)

    kept = [l.text for l in document_lines(document)]
    assert not any("VERMA" in t for t in kept)
    assert len(kept) == 9


def test_repeated_mixed_case_body_lines_are_kept():
    # an identical clause line at the top of several pages is content, not a header
    pages = [("(2) Nothing in this article shall affect the operation of any law.\n" if n % 3 == 0 else "")
             + f"Distinct article text number {n} goes here." for n in range(10)]
    document, _ = make_document(pages)

    assert sum(l.text.startswith("(2) Nothing") for l in document_lines(document)) == 4


def test_mixed_case_line_on_most_pages_is_furniture():
    bodies = ["The notification was issued.", "Objections were invited.", "Hearings were held in May.",
              "The rules came into force.", "Appeals lie to the tribunal.", "Copies are available."]
    pages = [f"The Gazette of India\n{body}" for body in bodies]
    document, _ = make_document(pages)

    assert [l.text for l in document_lines(document)] == bodies


def test_empty_pages_contribute_no_lines_and_keep_page_numbers():
    document, _ = make_document(["First page text.", "", "   \n  ", "Fourth page text."])

    assert document_lines(document) == [Line(1, "First page text."), Line(4, "Fourth page text.")]
