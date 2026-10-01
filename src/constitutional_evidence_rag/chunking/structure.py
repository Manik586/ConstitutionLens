"""Structure detection over Phase 1 page text (SRS FR-03; D16).

Turns a ParsedDocument into an ordered list of `Section`s — contiguous runs
of lines that share detected structure (an article, a headed judgment
section, front matter). The chunker then packs each section into chunks
without ever crossing a section boundary.

Principles:
* Detect only explicit, checkable signals. When a signal is absent the
  field stays None, and a document with no usable signal becomes a single
  unstructured section (size-based fallback). Nothing is guessed.
* Judgment headings are recorded verbatim. Mapping text onto the
  facts/issues/arguments/reasoning/holding taxonomy (`section_type`,
  FR-JS-01) is V2 and is not done here.
* Line text is never rewritten. The only lines dropped are page furniture:
  running headers/footers, bare page numbers, and lone Supreme Court
  Reports margin letters (A-H).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from constitutional_evidence_rag.common.chunks import Division
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.common.pages import ParsedDocument


@dataclass(frozen=True)
class Line:
    page: int
    text: str


@dataclass
class Section:
    lines: list[Line]
    structural: bool
    division: Division | None = None
    part: str | None = None
    chapter: str | None = None
    article_number: str | None = None
    article_title: str | None = None
    heading: str | None = None
    opinion_author: str | None = None
    clause_starts: dict[int, str] = field(default_factory=dict)  # line index in `lines` -> "(1)"


# ------------------------------------------------------------------ page furniture

_EDGE = 2  # running headers/footers live in the first/last two lines of a page
_PAGE_NUMBER = re.compile(r"^[\s\-–—]*(?:page\s+)?\d{1,4}(?:\s+of\s+\d{1,4})?[\s\-–—]*$", re.I)
_MARGIN_LETTER = re.compile(r"^[A-Ha-h]$")


def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.strip().lower()))


def _fuzzy(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())[:12]


def _upper_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0


def _header_like(text: str) -> bool:
    return len(text.split()) <= 12 and _upper_ratio(text) >= 0.7


def document_lines(document: ParsedDocument) -> list[Line]:
    """All non-blank lines in reading order, minus page furniture.

    Furniture is recognised three ways:
    * page numbers / "Page x of y" at a page edge;
    * uppercase running headers (OCR-tolerant): a short, mostly-uppercase line whose first 12
      letters match an uppercase line at the edges of >= 3 pages. OCR renders one header many
      ways ("[VERMA, CJI.]", "[VERMA, CTI.]", "[VERMA, OI.J") and sometimes mid-page, so once a
      key is established, matching lines are dropped anywhere on the page;
    * mixed-case running lines: the same line (digits normalized) at the edges of >= 5 pages
      and at least half of all pages. The high bar is deliberate: a clause such as "(2) Nothing
      in this article shall affect ..." can legitimately recur at page tops, and body text must
      never be dropped as furniture.
    """
    pages = [(p.provenance.page_number, [l.strip() for l in p.text.splitlines() if l.strip()]) for p in document.pages]
    exact: Counter[str] = Counter()
    fuzzy: Counter[str] = Counter()
    for _, lines in pages:
        edges = lines[:_EDGE] + lines[-_EDGE:]
        exact.update({_norm(l) for l in edges})
        fuzzy.update({_fuzzy(l) for l in edges if _header_like(l) and len(_fuzzy(l)) >= 6})

    def exact_running(line: str) -> bool:
        key = _norm(line)
        return len(key.split()) <= 12 and exact[key] >= max(5, len(pages) / 2)

    def fuzzy_running(line: str) -> bool:
        key = _fuzzy(line)
        return len(key) >= 6 and fuzzy[key] >= 3 and _header_like(line)

    out: list[Line] = []
    for page_number, lines in pages:
        for i, line in enumerate(lines):
            at_edge = i < _EDGE or i >= len(lines) - _EDGE
            if _MARGIN_LETTER.match(line) or fuzzy_running(line):
                continue
            if at_edge and (_PAGE_NUMBER.match(line) or exact_running(line)):
                continue
            out.append(Line(page_number, line))  # type: ignore[arg-type]
    return out


def detect_sections(lines: list[Line], source_type: SourceType) -> list[Section]:
    """Sections for a document; a single unstructured section if nothing reliable is found."""
    if not lines:
        return []
    detected: list[Section] | None = None
    if source_type is SourceType.CONSTITUTIONAL_TEXT:
        detected = _constitution_sections(lines)
    elif source_type is SourceType.JUDGMENT:
        detected = _judgment_sections(lines)
    return detected if detected else [Section(lines=list(lines), structural=False)]


# ------------------------------------------------------------------ Constitution

_LEAD = r"(?:\d{0,2}\s?\[)?\*{0,3}"  # footnote/insertion markers, e.g. "1[21A." in the official text
_NUM = r"(?P<num>\d{1,3}[A-Z]{0,3})"
# Body article heading: "14. Equality before law.—The State shall ..." The dash after the title is
# what separates a body heading from a table-of-contents entry ("14. Equality before law.").
_ARTICLE = re.compile(rf"^{_LEAD}{_NUM}\.\s+(?P<title>[A-Z(‘'\"].{{1,160}}?)\.\s*(?:—|–|-{{1,2}})\s*(?P<rest>.*)$")
_OMITTED = re.compile(rf"^{_LEAD}{_NUM}\.\s*\[(?P<title>[^\]]{{2,160}}?)\.?\]\s*(?P<rest>(?:Rep\.|Omitted|Repealed).*)$")
_ARTICLE_START = re.compile(rf"^{_LEAD}{_NUM}\.\s+[A-Z(‘'\"]")
_PART = re.compile(r"^PART\s+(?P<num>[IVXLC]+[A-Z]?)\b\.?\s*(?:[—–-]+\s*(?P<title>\S.*))?$")
_CHAPTER = re.compile(r"^CHAPTER\s+(?P<num>[IVXLC]+)\b\.?\s*(?:[—–-]+\s*(?P<title>\S.*))?$")
_ORDINALS = "FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH"
_SCHEDULE = re.compile(rf"^(?:THE\s+)?(?:(?:{_ORDINALS})\s+)?SCHEDULE\b\s*(?:\[.*\])?$|^APPENDIX\b.*$")
_PREAMBLE = re.compile(r"^PREAMBLE$")
_WE_THE_PEOPLE = re.compile(r"^WE,?\s+THE\s+PEOPLE\s+OF\s+INDIA", re.I)
_CLAUSE = re.compile(r"^(?P<label>\(\d+[A-Z]?\))\s")
_TERMINAL = re.compile(r"[.;:?!][\"'”’)\]]*$")


def _article_key(num: str) -> tuple[int, str]:
    digits = re.match(r"\d+", num)
    return (int(digits.group(0)) if digits else -1, num[digits.end():] if digits else num)


def _match_article(lines: list[Line], i: int) -> tuple[str, str, str] | None:
    """(number, title, rest-of-heading) if line i starts an article, else None.
    Titles that wrap onto the next line are joined for matching only."""
    text = lines[i].text
    for pattern in (_ARTICLE, _OMITTED):
        m = pattern.match(text)
        if m:
            return m.group("num"), m.group("title").strip(), m.group("rest").strip()
    if _ARTICLE_START.match(text) and i + 1 < len(lines) and not _ARTICLE_START.match(lines[i + 1].text):
        m = _ARTICLE.match(f"{text} {lines[i + 1].text}")
        if m and len(m.group("title")) <= 160:
            return m.group("num"), m.group("title").strip(), m.group("rest").strip()
    return None


def _is_upper_title(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8 and len(text.split()) <= 12


def _strip_insertion_marks(line: str) -> str:
    """'4[Saving of Certain Laws]' -> 'Saving of Certain Laws' (amendment footnote markers)."""
    return re.sub(r"^\d{0,2}\s?\[", "", line).rstrip("]").strip() or line


def _is_subheading(line: str) -> bool:
    line = _strip_insertion_marks(line)
    words = line.split()
    return (
        1 <= len(words) <= 8
        and line[0].isupper()
        and not re.search(r"[.,;:—–-]$", line)
        and not _CLAUSE.match(line)
        and not _ARTICLE_START.match(line)
        and any(c.isalpha() for c in line)
    )


def _constitution_sections(lines: list[Line]) -> list[Section] | None:
    first_article = next((i for i in range(len(lines)) if _match_article(lines, i)), None)
    if first_article is None:
        return None

    # Body starts at the PART/CHAPTER heading block just before the first article (so a table of
    # contents, which has PART lines too, stays front matter), or at the Preamble if there is one.
    body_start = first_article
    for j in range(first_article - 1, max(first_article - 7, -1), -1):
        text = lines[j].text
        if _PART.match(text) or _CHAPTER.match(text):
            body_start = j
        elif not (_is_upper_title(text) or _is_subheading(text)):
            break
    for j in range(body_start - 1, -1, -1):
        if _WE_THE_PEOPLE.match(lines[j].text):
            body_start = j - 1 if j > 0 and _PREAMBLE.match(lines[j - 1].text) else j
            break

    sections: list[Section] = []
    if body_start > 0:
        sections.append(Section(lines=lines[:body_start], structural=True, division=Division.FRONT_MATTER))

    part = chapter = subheading = None
    in_schedule = False
    last_key: tuple[int, str] | None = None
    current = Section(lines=[], structural=True, division=Division.BODY)
    if _PREAMBLE.match(lines[body_start].text) or _WE_THE_PEOPLE.match(lines[body_start].text):
        current.heading = "Preamble"

    def start(**kw) -> Section:
        nonlocal current
        if current.lines:
            sections.append(current)
        current = Section(lines=[], structural=True, division=Division.BODY, part=part, chapter=chapter, **kw)
        return current

    i = body_start
    while i < len(lines):
        line = lines[i]
        text = line.text
        part_m, chapter_m = _PART.match(text), _CHAPTER.match(text)
        if _SCHEDULE.match(text):
            in_schedule, part, chapter, subheading = True, None, None, None
            start(heading=text)
        elif in_schedule:
            pass  # schedules contain numbered paragraphs that look like articles: no article detection
        elif part_m:
            title = part_m.group("title")
            if not title and i + 1 < len(lines) and _is_upper_title(lines[i + 1].text) and not _match_article(lines, i + 1):
                title = lines[i + 1].text
            part = f"PART {part_m.group('num')}" + (f" — {title}" if title else "")
            chapter = subheading = None
            start()
        elif chapter_m:
            chapter, subheading = text, None
            start()
        else:
            article = _match_article(lines, i)
            if article and (last_key is None or _article_key(article[0]) > last_key):
                num, title, rest = article
                carried: list[Line] = []
                # A short title line right before the article ("Right to Equality") is a subheading;
                # it applies to the following articles until the next subheading, Part or Chapter.
                if current.lines and _is_subheading(last := current.lines[-1].text):
                    if current.article_number:
                        ok = len(current.lines) >= 2 and bool(_TERMINAL.search(current.lines[-2].text))
                    else:  # after a Part/Chapter heading block: anything but the Part's own title line
                        ok = len(current.lines) >= 2 and not _is_upper_title(last)
                    if ok:
                        carried = [current.lines.pop()]
                        subheading = _strip_insertion_marks(carried[0].text)
                section = start(article_number=num, article_title=title, heading=subheading)
                section.lines.extend(carried)
                last_key = _article_key(num)
                clause = _CLAUSE.match(rest + " ")
                if clause:
                    section.clause_starts[len(section.lines)] = clause.group("label")
            elif current.article_number and (clause := _CLAUSE.match(text)):
                current.clause_starts[len(current.lines)] = clause.group("label")
        current.lines.append(line)
        i += 1
    if current.lines:
        sections.append(current)
    return _merge_title_only(sections)


def _merge_title_only(sections: list[Section]) -> list[Section]:
    """A body section holding only Part/Chapter heading lines ("PART III", "FUNDAMENTAL RIGHTS")
    joins the next section, so no chunk is a bare title."""
    out: list[Section] = []
    for k, section in enumerate(sections):
        nxt = sections[k + 1] if k + 1 < len(sections) else None
        title_only = (
            section.division is Division.BODY and section.article_number is None and section.heading is None
            and all(_PART.match(l.text) or _CHAPTER.match(l.text) or _is_upper_title(l.text) for l in section.lines)
        )
        if title_only and nxt is not None and nxt.division is Division.BODY:
            offset = len(section.lines)
            nxt.lines = section.lines + nxt.lines
            nxt.clause_starts = {i + offset: label for i, label in nxt.clause_starts.items()}
            continue
        out.append(section)
    return out


# ------------------------------------------------------------------ judgments

_DELIVERED = re.compile(
    r"judgments?\s+of\s+the\s+court\s+(?:was|were)\s+delivered"
    r"|following\s+judgments?\s+(?:of\s+the\s+court\s+)?(?:was|were)\s+delivered",
    re.I,
)
_JUDGMENT_TITLE = re.compile(r"^(?:J\s*U\s*D\s*G\s*M\s*E\s*N\s*T|O\s*R\s*D\s*E\s*R)$")
# Opinion author at the start of a line: "VERMA, CJI. This Writ ...", "B.P. JEEVAN REDDY, J. ...",
# or a reporter's header line "DELIVERED BY B.P. JEEVAN REDDY, J.". "CJI" allows no inner spaces,
# so "AHMADI, CJ. I have had ..." yields "AHMADI, CJ.", not "AHMADI, CJ. I".
_NAME = r"(?:[A-Z]\.\s?){0,4}[A-Z][A-Z'’-]+(?:\s+[A-Z][A-Z'’-]+){0,2},\s*(?:C\.?J\.?I\.?|C\.?\s?J\.|J\.)"
_AUTHOR = re.compile(rf"^(?:DELIVERED\s+BY\s+)?(?P<author>{_NAME})(?=\s|$)")
_ENUM = r"(?:(?:[A-Z]|[IVXLC]{1,6}|\d{1,2})[.)]\s+|\((?:[a-z]|[ivx]{1,5}|\d{1,2})\)\s+)?"
_VOCAB = (
    r"(?:brief\s+facts|facts|factual\s+(?:background|matrix)|background|introduction|preface"
    r"|issues?|questions?\s+(?:of\s+law|for|involved|arising)|points?\s+(?:for|of)\s+(?:consideration|determination)"
    r"|submissions?|arguments?|contentions?|rival\s+(?:submissions|contentions)|analysis|discussion"
    r"|consideration|reasoning|findings?|conclusions?|summary|held|order|final\s+order|directions?"
    r"|guidelines|relief|disposition|epilogue|the\s+law|legal\s+position|precedents)"
)
_VOCAB_HEADING = re.compile(rf"^{_ENUM}{_VOCAB}\b", re.I)
_INLINE_HEADING = re.compile(r"^(?P<h>HELD|FACTS|ISSUES?|ORDER|CONCLUSIONS?|DIRECTIONS)\s*[:—–]\s*\S")


def _heading(lines: list[Line], i: int) -> str | None:
    """A heading starting at line i, verbatim, or None. Conservative on purpose."""
    text = lines[i].text
    inline = _INLINE_HEADING.match(text)
    if inline:
        return inline.group("h")
    if not _VOCAB_HEADING.match(text) or len(text.split()) > 10 or len(text) > 90:
        return None
    if not text[0].isupper() and not text[0].isdigit() and text[0] != "(":
        return None  # a heading never starts mid-sentence ("order accordingly.")
    if re.search(r"[,;]", text) or re.search(r"[,;\-–—]$", text):
        return None
    if text.endswith(".") and len(text.split()) > 1 and not _is_upper_title(text):
        return None  # "Order accordingly." is a sentence; "Conclusion." or "ORDER" is a heading
    prev = lines[i - 1].text if i > 0 else ""
    # a heading follows a sentence end, an uppercase title line, or another heading-like line
    prev_ok = not prev or bool(_TERMINAL.search(prev)) or _is_upper_title(prev) or _is_subheading(prev)
    nxt = lines[i + 1].text if i + 1 < len(lines) else ""
    next_ok = not nxt or nxt[0].isupper() or nxt[0] in "(\"'‘“" or nxt[0].isdigit()
    return text.rstrip(":").strip() if prev_ok and next_ok else None


def _judgment_sections(lines: list[Line]) -> list[Section] | None:
    opinion_start = next(
        (i for i, l in enumerate(lines)
         if _DELIVERED.search(l.text) or _JUDGMENT_TITLE.match(l.text) or _AUTHOR.match(l.text)),
        None,
    )
    sections: list[Section] = []
    division = Division.FRONT_MATTER if opinion_start is not None else None
    current = Section(lines=[], structural=True, division=division)
    author: str | None = None
    found_structure = opinion_start is not None

    def start(**kw) -> None:
        nonlocal current
        if current.lines:
            sections.append(current)
        current = Section(lines=[], structural=True, division=division, opinion_author=author, **kw)

    for i, line in enumerate(lines):
        if i == opinion_start:
            division = Division.OPINION
            start()
        in_opinion = division is Division.OPINION
        author_m = _AUTHOR.match(line.text) if in_opinion else None
        heading = _heading(lines, i)
        if author_m and (author_m.group("author") != author or current.lines):
            author = author_m.group("author").strip()
            if current.opinion_author is None and current.heading is None and len(current.lines) <= 6:
                # delimiter / reporter header lines belong with the opinion they introduce
                current.opinion_author = author
            else:
                start()
        elif heading:
            found_structure = True
            start(heading=heading)
        current.lines.append(line)
    if current.lines:
        sections.append(current)
    return _merge_heading_only(sections) if found_structure else None


def _merge_heading_only(sections: list[Section]) -> list[Section]:
    """A section holding nothing but a heading line, or only title/delimiter lines
    ("J U D G M E N T", "The Judgment of the Court was delivered by"), joins the next section
    in the same division, so no chunk is a bare title. The next section keeps its own
    (more specific) heading and author when it has them."""
    out: list[Section] = []
    pending: Section | None = None
    for section in sections:
        if pending is not None:
            if section.division == pending.division:
                section.lines = pending.lines + section.lines
                section.heading = section.heading or pending.heading
                section.opinion_author = section.opinion_author or pending.opinion_author
            else:
                out.append(pending)
            pending = None
        title_only = all(_JUDGMENT_TITLE.match(l.text) or _DELIVERED.search(l.text) for l in section.lines)
        if (section.heading and len(section.lines) == 1) or title_only:
            pending = section
            continue
        out.append(section)
    if pending is not None:
        out.append(pending)
    return out
