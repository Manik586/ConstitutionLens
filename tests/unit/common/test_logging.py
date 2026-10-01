import logging

from constitutional_evidence_rag.common.logging import configure_logging, get_logger


def test_get_logger_returns_named_logger():
    logger = get_logger("constitutional_evidence_rag.test")

    assert logger.name == "constitutional_evidence_rag.test"


def test_configure_logging_sets_root_level():
    configure_logging("DEBUG", force=True)
    assert logging.getLogger().level == logging.DEBUG

    # Restore a sane default so this test doesn't leak state into others.
    configure_logging("INFO", force=True)
    assert logging.getLogger().level == logging.INFO
