"""Phase 7 fixtures: PDFs generated at test time (no committed binaries), per-test storage
roots, and a service wired to the deterministic test embedder (no model download)."""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import yaml

from constitutional_evidence_rag.common.config import AppSettings, LoggingSettings, PathsSettings, Settings, UploadSettings

ANSWERING = Path(__file__).resolve().parents[1] / "answering"
if str(ANSWERING) not in sys.path:
    sys.path.insert(0, str(ANSWERING))

from answer_fixtures import HashingEmbedder  # noqa: E402  (reused from the Phase 4-6 tests)

REPO_ROOT = Path(__file__).resolve().parents[3]

A_PAGES = ["Zeppelin hangar regulations require an inspection of every airship every thirty days.",
           "Crews must record zeppelin altitude readings in the flight log after each landing."]
B_PAGES = ["The marmalade factory standards require citrus quality checks before every batch.",
           "Marmalade jars must be sealed while hot and labelled with the batch number."]


def pdf_bytes(pages: list[str]) -> bytes:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 520, 760), text, fontsize=11)
    data = document.tobytes()
    document.close()
    return data


def scanned_pdf_bytes() -> bytes:
    """A page with only graphics, like an image-only scan: no extractable text."""
    document = pymupdf.open()
    page = document.new_page()
    page.draw_rect(pymupdf.Rect(72, 72, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    data = document.tobytes()
    document.close()
    return data


def encrypted_pdf_bytes() -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "secret text")
    data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    document.close()
    return data


def make_settings(tmp_path: Path, **uploads) -> Settings:
    return Settings(app=AppSettings(name="t", version="0", phase="v1"), logging=LoggingSettings(),
                    paths=PathsSettings(data_raw_dir=tmp_path / "raw", data_processed_dir=tmp_path / "processed",
                                        indexes_dir=tmp_path / "indexes", uploads_dir=tmp_path / "uploads"),
                    uploads=UploadSettings(**uploads))


def write_config(tmp_path: Path) -> Path:
    config = yaml.safe_load((REPO_ROOT / "configs" / "v1.yaml").read_text())
    config["paths"] = {"data_raw_dir": str(tmp_path / "raw"), "data_processed_dir": str(tmp_path / "processed"),
                       "indexes_dir": str(tmp_path / "indexes"), "uploads_dir": str(tmp_path / "uploads")}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def embedder_factory(settings=None):
    return HashingEmbedder()
