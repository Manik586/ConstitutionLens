import os

import pytest

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.corpora.errors import (
    CorpusBusyError, CorpusExistsError, CorpusNotFoundError, CorpusNotReadyError, InvalidCorpusIdError, ReservedCorpusError,
)
from constitutional_evidence_rag.corpora.models import CorpusStatus
from constitutional_evidence_rag.corpora.registry import CORPUS_FILENAME, LOCK_FILENAME, CorpusRegistry, validate_corpus_id


@pytest.fixture
def registry(settings):
    return CorpusRegistry(settings.paths)


def test_create_get_list(registry):
    created = registry.create("my-research", "My research")
    assert (created.status, created.document_count, created.reserved) == (CorpusStatus.UPLOADED, 0, False)
    assert registry.get("my-research") == created
    assert [c.corpus_id for c in registry.list()] == [CURATED_CORPUS_ID, "my-research"]  # curated always listed first


def test_status_transitions_are_persisted(registry):
    registry.create("c1")
    for status in (CorpusStatus.PROCESSING, CorpusStatus.READY, CorpusStatus.FAILED, CorpusStatus.UPLOADED):
        assert registry.update("c1", status=status).status is status
        assert registry.get("c1").status is status


@pytest.mark.parametrize("bad", ["..", "a/b", "a\\b", "Upper", "", "-x", "x-", "a" * 65, "spa ce", "../etc", None, 42])
def test_invalid_ids(registry, bad):
    with pytest.raises(InvalidCorpusIdError):
        validate_corpus_id(bad)
    with pytest.raises(InvalidCorpusIdError):
        registry.create(bad)


def test_reserved_curated_corpus(registry):
    for action in (lambda: registry.create(CURATED_CORPUS_ID), lambda: registry.delete(CURATED_CORPUS_ID),
                   lambda: registry.save(registry.get(CURATED_CORPUS_ID))):
        with pytest.raises(ReservedCorpusError):
            action()
    assert not (registry.processed_root / CURATED_CORPUS_ID / CORPUS_FILENAME).exists()  # never gets a corpus.json


def test_duplicate_and_missing(registry):
    registry.create("dup")
    with pytest.raises(CorpusExistsError, match="already exists"):
        registry.create("dup")
    with pytest.raises(CorpusNotFoundError, match="Corpus 'nope' does not exist."):
        registry.get("nope")


def test_curated_status_is_inferred(registry, curated):
    corpus = registry.get(CURATED_CORPUS_ID)
    assert (corpus.status, corpus.reserved, corpus.chunk_count) == (CorpusStatus.READY, True, len(curated))


def test_curated_without_indexes_is_uploaded(registry):
    assert registry.get(CURATED_CORPUS_ID).status is CorpusStatus.UPLOADED


def test_corpus_json_write_is_atomic(registry, monkeypatch):
    registry.create("atomic")
    before = (registry.processed_dir("atomic") / CORPUS_FILENAME).read_text()

    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        registry.update("atomic", status=CorpusStatus.READY)
    monkeypatch.undo()
    assert (registry.processed_dir("atomic") / CORPUS_FILENAME).read_text() == before  # untouched
    assert [p.name for p in registry.processed_dir("atomic").iterdir()] == [CORPUS_FILENAME]  # no temp files left


def test_ensure_queryable_messages(registry):
    registry.create("my-research")
    cases = [(CorpusStatus.PROCESSING, "Corpus 'my-research' is still processing."),
             (CorpusStatus.UPLOADED, "Corpus 'my-research' has not been built yet; build it before querying.")]
    for status, message in cases:
        registry.update("my-research", status=status)
        with pytest.raises(CorpusNotReadyError) as exc:
            registry.ensure_queryable("my-research")
        assert exc.value.message == message and exc.value.status is status
    registry.update("my-research", status=CorpusStatus.FAILED, error="the documents produced no text chunks")
    with pytest.raises(CorpusNotReadyError, match="Corpus 'my-research' failed to build: the documents produced no text chunks."):
        registry.ensure_queryable("my-research")
    registry.update("my-research", status=CorpusStatus.READY)
    assert registry.ensure_queryable("my-research").status is CorpusStatus.READY


def test_lock_excludes_concurrent_builds_and_cleans_up(registry):
    registry.create("locked")
    with registry.lock("locked"):
        assert (registry.processed_dir("locked") / LOCK_FILENAME).exists()
        with pytest.raises(CorpusBusyError):
            with registry.lock("locked"):
                pass
    assert not (registry.processed_dir("locked") / LOCK_FILENAME).exists()
    with registry.lock("locked"):  # free again
        pass


def test_lock_file_from_another_process_blocks(registry):
    registry.create("other")
    (registry.processed_dir("other") / LOCK_FILENAME).write_text('{"pid": 1}')
    with pytest.raises(CorpusBusyError):
        with registry.lock("other"):
            pass


def test_stale_processing_is_recovered(registry):
    registry.create("crashed")
    registry.update("crashed", status=CorpusStatus.PROCESSING)
    (registry.processed_dir("crashed") / LOCK_FILENAME).write_text('{"pid": 999999}')
    registry.create("fine")

    assert registry.recover_stale() == ["crashed"]
    crashed = registry.get("crashed")
    assert crashed.status is CorpusStatus.FAILED and "interrupted" in crashed.error
    assert not (registry.processed_dir("crashed") / LOCK_FILENAME).exists()
    assert registry.get("fine").status is CorpusStatus.UPLOADED


def test_delete_removes_all_storage(registry):
    registry.create("gone")
    for d in (registry.indexes_dir("gone"), registry.uploads_dir("gone")):
        d.mkdir(parents=True)
        (d / "f").write_text("x")
    registry.delete("gone")
    assert not registry.processed_dir("gone").exists() and not registry.indexes_dir("gone").exists()
    assert not registry.uploads_dir("gone").exists()
    with pytest.raises(CorpusNotFoundError):
        registry.get("gone")
