import os
import re
import stat
import sys
from datetime import date

import pytest

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, SourceType
from constitutional_evidence_rag.ingestion.metadata import (
    ManifestError,
    RegistryError,
    latest_version,
    load_manifest,
    load_registry,
    make_document_id,
    normalize_source_url,
    version_key,
    write_registry,
)

from pdf_fixtures import CONSTITUTION_ENTRY, JUDGMENT_ENTRY, sample_document, write_manifest

URL = "https://example.org/judgments/synthetic-1"
CORE = CURATED_CORPUS_ID


# ---------------------------------------------------------------- document IDs


def test_document_id_is_deterministic_and_prefixed():
    first = make_document_id(CORE, SourceType.JUDGMENT, URL)

    assert first == make_document_id(CORE, SourceType.JUDGMENT, URL)
    assert re.fullmatch(r"JUDG-[0-9a-f]{12}", first)
    assert make_document_id(CORE, SourceType.CONSTITUTIONAL_TEXT, URL).startswith("CONST-")
    assert make_document_id(CORE, SourceType.AMENDMENT, URL).startswith("AMEND-")


def test_document_id_differs_by_source_and_type():
    assert make_document_id(CORE, SourceType.JUDGMENT, URL) != make_document_id(CORE, SourceType.JUDGMENT, URL + "-2")
    assert make_document_id(CORE, SourceType.JUDGMENT, URL)[5:] != make_document_id(CORE, SourceType.AMENDMENT, URL)[6:]


def test_document_id_is_scoped_to_its_corpus():
    # The same judgment in the curated corpus and in a (V2) user collection
    # must never share an ID (D15).
    assert make_document_id(CORE, SourceType.JUDGMENT, URL) != make_document_id("my-collection", SourceType.JUDGMENT, URL)


@pytest.mark.parametrize(
    "variant",
    [
        "https://example.org/judgments/synthetic-1/",
        "HTTPS://EXAMPLE.ORG/judgments/synthetic-1",
        "  https://example.org/judgments/synthetic-1#para-12 ",
    ],
)
def test_trivial_url_variants_map_to_same_id(variant):
    assert make_document_id(CORE, SourceType.JUDGMENT, variant) == make_document_id(CORE, SourceType.JUDGMENT, URL)


def test_url_path_case_is_preserved():
    # Paths can be case-sensitive on real servers, so they must not be folded.
    assert normalize_source_url("https://x.org/Doc") != normalize_source_url("https://x.org/doc")


def test_version_key_format():
    assert version_key("JUDG-abc", 2) == "JUDG-abc@v2"


# -------------------------------------------------------------------- manifest


def test_load_valid_manifest(tmp_path):
    path = write_manifest(tmp_path / "m.yaml", [CONSTITUTION_ENTRY, JUDGMENT_ENTRY])

    manifest = load_manifest(path)

    assert [e.source_type for e in manifest.documents] == [SourceType.CONSTITUTIONAL_TEXT, SourceType.JUDGMENT]
    assert manifest.documents[1].source_date == date(2020, 1, 15)
    assert manifest.documents[1].spec.title == JUDGMENT_ENTRY["title"]
    assert "file" not in manifest.documents[1].spec.model_dump()  # spec is the path-free, generic part


def test_empty_manifest_is_valid(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("documents: []\n")

    assert load_manifest(path).documents == []


def test_missing_manifest(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(tmp_path / "absent.yaml")


@pytest.mark.parametrize(
    "override",
    [
        {"source_type": "high_court_order"},  # not a V1 source type (FR-04)
        {"title": ""},
        {"source_url": "example.org/no-scheme"},
        {"file": "/abs/path.pdf"},
        {"file": "../outside.pdf"},
        {"unexpected": "field"},
    ],
)
def test_invalid_manifest_entries_are_rejected(tmp_path, override):
    path = write_manifest(tmp_path / "m.yaml", [{**JUDGMENT_ENTRY, **override}])

    with pytest.raises(ManifestError):
        load_manifest(path)


@pytest.mark.parametrize("entry", [JUDGMENT_ENTRY, CONSTITUTION_ENTRY])
def test_source_date_is_required_for_judgments_and_constitution(tmp_path, entry):
    undated = {k: v for k, v in entry.items() if k != "source_date"}
    path = write_manifest(tmp_path / "m.yaml", [undated])

    with pytest.raises(ManifestError, match="source_date is required") as exc_info:
        load_manifest(path)
    assert f"entry '{entry['file']}'" in str(exc_info.value)  # the error names the offending entry


def test_explicit_null_source_date_is_also_rejected(tmp_path):
    path = write_manifest(tmp_path / "m.yaml", [{**JUDGMENT_ENTRY, "source_date": None}])

    with pytest.raises(ManifestError, match="source_date is required"):
        load_manifest(path)


def test_source_date_stays_optional_for_amendments(tmp_path):
    amendment = {
        "file": "amendments/example.pdf",
        "source_type": "amendment",
        "title": "Example Amendment Act",
        "source_url": "https://example.org/amendments/example",
    }
    path = write_manifest(tmp_path / "m.yaml", [amendment])

    assert load_manifest(path).documents[0].source_date is None


def test_old_judgment_and_current_constitution_keep_their_own_dates(tmp_path):
    old_judgment = {**JUDGMENT_ENTRY, "source_date": "1973-04-24"}
    path = write_manifest(tmp_path / "m.yaml", [CONSTITUTION_ENTRY, old_judgment])

    constitution, judgment = load_manifest(path).documents

    assert (constitution.source_date, judgment.source_date) == (date(2026, 1, 1), date(1973, 4, 24))


def test_invalid_date_format_is_rejected(tmp_path):
    path = write_manifest(tmp_path / "m.yaml", [{**JUDGMENT_ENTRY, "source_date": "24/04/1973"}])

    with pytest.raises(ManifestError):
        load_manifest(path)


def test_manifest_rejects_the_same_source_listed_twice(tmp_path):
    duplicate = {**JUDGMENT_ENTRY, "file": "judgments/copy.pdf", "source_url": URL + "/"}
    path = write_manifest(tmp_path / "m.yaml", [JUDGMENT_ENTRY, duplicate])

    with pytest.raises(ManifestError, match="same source"):
        load_manifest(path)


def test_malformed_yaml(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("documents: [ {file: a.pdf\n")

    with pytest.raises(ManifestError):
        load_manifest(path)


# -------------------------------------------------------------------- registry


def test_registry_round_trip_and_latest_version(tmp_path):
    path = tmp_path / "metadata.jsonl"
    v1 = sample_document(document_version=1, replaced_by="JUDG-000000000001@v2")
    v2 = sample_document(document_version=2)
    other = sample_document(document_id="CONST-000000000009", source_type=SourceType.CONSTITUTIONAL_TEXT)

    write_registry(path, [v2, other, v1])
    loaded = load_registry(path)

    assert loaded == [other, v1, v2]  # sorted by (document_id, version)
    assert latest_version(loaded, "JUDG-000000000001") == v2
    assert latest_version(loaded, "JUDG-unknown") is None


def test_missing_registry_is_empty(tmp_path):
    assert load_registry(tmp_path / "metadata.jsonl") == []


def test_malformed_registry_row_reports_line(tmp_path):
    path = tmp_path / "metadata.jsonl"
    write_registry(path, [sample_document()])
    with path.open("a") as f:
        f.write('{"document_id": "broken"}\n')

    with pytest.raises(RegistryError, match=":2:"):
        load_registry(path)


def test_duplicate_registry_version_is_rejected(tmp_path):
    path = tmp_path / "metadata.jsonl"
    row = sample_document().model_dump_json()
    path.write_text(row + "\n" + row + "\n")

    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(path)


def test_atomic_write_leaves_no_temp_files(tmp_path):
    write_registry(tmp_path / "metadata.jsonl", [sample_document()])

    assert [p.name for p in tmp_path.iterdir()] == ["metadata.jsonl"]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Unix-only: asserts exact POSIX mode bits under a umask; on Windows os.chmod/os.umask only "
           "control the read-only flag (covered by test_atomic_write_leaves_a_readable_writable_file)",
)
def test_atomic_write_uses_normal_file_permissions(tmp_path):
    path = tmp_path / "metadata.jsonl"
    umask = os.umask(0o022)
    try:
        write_registry(path, [sample_document()])
        assert path.stat().st_mode & 0o777 == 0o644  # not mkstemp's 0600

        os.chmod(path, 0o640)
        write_registry(path, [sample_document()])
        assert path.stat().st_mode & 0o777 == 0o640  # an existing file keeps its mode
    finally:
        os.umask(umask)


def test_atomic_write_leaves_a_readable_writable_file(tmp_path):
    # Cross-platform: the temp file from mkstemp is owner-only (0600 on Unix); the file left in
    # place must be an ordinary readable/writable one on every OS, including Windows, where
    # "permissions" reduce to the read-only flag.
    path = tmp_path / "metadata.jsonl"
    write_registry(path, [sample_document()])
    write_registry(path, [sample_document()])  # rewriting over an existing file keeps it usable

    assert os.access(path, os.R_OK) and os.access(path, os.W_OK)
    assert path.stat().st_mode & stat.S_IREAD and path.stat().st_mode & stat.S_IWRITE  # not read-only
    assert len(load_registry(path)) == 1
