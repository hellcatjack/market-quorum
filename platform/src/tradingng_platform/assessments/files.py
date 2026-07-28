from __future__ import annotations

import shutil
import uuid
from pathlib import Path


def delete_run_directory(root: Path, run_id: uuid.UUID) -> bool:
    """Delete one UUID-named directory without following a child symlink."""
    resolved_root = root.resolve()
    candidate = resolved_root / str(run_id)

    if candidate.is_symlink():
        candidate.unlink()
        return True
    if not candidate.exists():
        return False

    resolved_candidate = candidate.resolve()
    if resolved_candidate.parent != resolved_root:
        raise ValueError("run directory escapes configured root")
    if not resolved_candidate.is_dir():
        raise ValueError("run storage path is not a directory")

    shutil.rmtree(resolved_candidate)
    return True
