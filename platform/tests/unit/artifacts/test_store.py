import hashlib
import uuid

import pytest

from tradingng_platform.artifacts.store import LocalArtifactStore


def test_store_is_content_addressed_and_verifiable(tmp_path):
    source = tmp_path / "report.md"
    source.write_text("redacted report", encoding="utf-8")
    store = LocalArtifactStore(tmp_path / "artifacts")

    saved = store.put(uuid.UUID(int=1), "complete_report", "text/markdown", source)

    assert saved.sha256 == hashlib.sha256(b"redacted report").hexdigest()
    assert saved.path.read_bytes() == b"redacted report"
    assert store.verify(saved.storage_key, saved.sha256)
    assert not list((tmp_path / "artifacts").rglob("*.tmp"))


def test_store_rejects_unsafe_artifact_kinds(tmp_path):
    source = tmp_path / "report.md"
    source.write_text("report", encoding="utf-8")
    store = LocalArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="invalid artifact kind"):
        store.put(uuid.UUID(int=1), "../../escape", "text/markdown", source)


def test_resolve_rejects_keys_outside_storage_root(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="escapes storage root"):
        store.resolve("../../escape")
