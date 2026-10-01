"""Shared ingestion fixtures. Builders live in pdf_fixtures.py (a plain
module, since the tests tree has no __init__.py files)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_fixtures import (
    CONSTITUTION_ENTRY,
    CONSTITUTION_PAGES,
    JUDGMENT_ENTRY,
    JUDGMENT_PAGES,
    write_manifest,
    write_pdf,
)


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, Path]:
    """A raw dir with a 2-page constitution PDF, a 3-page judgment PDF
    (page 2 blank, printed labels 225-227) and a manifest listing both."""
    raw = tmp_path / "raw"
    write_pdf(raw / CONSTITUTION_ENTRY["file"], CONSTITUTION_PAGES)
    write_pdf(raw / JUDGMENT_ENTRY["file"], JUDGMENT_PAGES, first_page_label=225)
    manifest = write_manifest(raw / "manifest.yaml", [CONSTITUTION_ENTRY, JUDGMENT_ENTRY])
    return {"raw": raw, "processed": tmp_path / "processed", "manifest": manifest}
