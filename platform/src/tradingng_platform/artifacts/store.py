import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from tradingng_platform.assessments.files import delete_run_directory

_ARTIFACT_KIND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_COPY_BUFFER_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StoredArtifact:
    kind: str
    media_type: str
    size: int
    sha256: str
    storage_key: str
    path: Path


class LocalArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        run_id: uuid.UUID,
        kind: str,
        media_type: str,
        source: Path,
    ) -> StoredArtifact:
        if not _ARTIFACT_KIND.fullmatch(kind):
            raise ValueError(f"invalid artifact kind: {kind!r}")

        destination_dir = (self.root / str(run_id) / kind).resolve()
        if not destination_dir.is_relative_to(self.root):
            raise ValueError("artifact destination escapes storage root")
        destination_dir.mkdir(parents=True, exist_ok=True)

        temporary = destination_dir / f".{uuid.uuid4().hex}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                while chunk := reader.read(_COPY_BUFFER_SIZE):
                    writer.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())

            sha256 = digest.hexdigest()
            destination = destination_dir / sha256
            temporary.replace(destination)
            self._fsync_directory(destination_dir)
        finally:
            temporary.unlink(missing_ok=True)

        storage_key = destination.relative_to(self.root).as_posix()
        return StoredArtifact(
            kind=kind,
            media_type=media_type,
            size=size,
            sha256=sha256,
            storage_key=storage_key,
            path=destination,
        )

    def resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("artifact key escapes storage root")
        return candidate

    def verify(self, storage_key: str, expected_sha256: str) -> bool:
        path = self.resolve(storage_key)
        if not path.is_file():
            return False

        digest = hashlib.sha256()
        with path.open("rb") as artifact:
            while chunk := artifact.read(_COPY_BUFFER_SIZE):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256

    def delete_run(self, run_id: uuid.UUID) -> bool:
        return delete_run_directory(self.root, run_id)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
