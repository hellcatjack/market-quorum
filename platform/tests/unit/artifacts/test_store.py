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


def test_delete_run_removes_only_the_run_directory(tmp_path):
    run_id = uuid.UUID(int=7)
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_file = store.root / str(run_id) / "report" / "result.md"
    run_file.parent.mkdir(parents=True)
    run_file.write_text("report", encoding="utf-8")
    neighbor = store.root / str(uuid.UUID(int=8)) / "keep.txt"
    neighbor.parent.mkdir(parents=True)
    neighbor.write_text("keep", encoding="utf-8")

    assert store.delete_run(run_id) is True
    assert not run_file.parent.parent.exists()
    assert neighbor.read_text(encoding="utf-8") == "keep"
    assert store.delete_run(run_id) is False


def test_delete_run_unlinks_symlink_without_following_it(tmp_path):
    run_id = uuid.UUID(int=9)
    store = LocalArtifactStore(tmp_path / "artifacts")
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "protected.txt"
    protected.write_text("keep", encoding="utf-8")
    (store.root / str(run_id)).symlink_to(outside, target_is_directory=True)

    assert store.delete_run(run_id) is True
    assert not (store.root / str(run_id)).exists()
    assert protected.read_text(encoding="utf-8") == "keep"
