import re
from datetime import date

import pytest

from constitutional_evidence_rag.common.models import SourceType
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


# ---------------------------------------------------------------- document IDs


def test_document_id_is_deterministic_and_prefixed():
    first = make_document_id(SourceType.JUDGMENT, URL)

    assert first == make_document_id(SourceType.JUDGMENT, URL)
    assert re.fullmatch(r"JUDG-[0-9a-f]{12}", first)
    assert make_document_id(SourceType.CONSTITUTIONAL_TEXT, URL).startswith("CONST-")
    assert make_document_id(SourceType.AMENDMENT, URL).startswith("AMEND-")


def test_document_id_differs_by_source_and_type():
    assert make_document_id(SourceType.JUDGMENT, URL) != make_document_id(SourceType.JUDGMENT, URL + "-2")
    assert make_document_id(SourceType.JUDGMENT, URL)[5:] != make_document_id(SourceType.AMENDMENT, URL)[6:]


@pytest.mark.parametrize(
    "variant",
    [
        "https://example.org/judgments/synthetic-1/",
        "HTTPS://EXAMPLE.ORG/judgments/synthetic-1",
        "  https://example.org/judgments/synthetic-1#para-12 ",
    ],
)
def test_trivial_url_variants_map_to_same_id(variant):
    assert make_document_id(SourceType.JUDGMENT, variant) == make_document_id(SourceType.JUDGMENT, URL)


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
    assert manifest.documents[1].document_id == make_document_id(SourceType.JUDGMENT, JUDGMENT_ENTRY["source_url"])


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
