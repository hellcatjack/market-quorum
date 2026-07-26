import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_backup_and_restore_shell_are_syntactically_guarded():
    scripts = [ROOT / "scripts/backup_platform.sh", ROOT / "scripts/restore_platform.sh"]
    for script in scripts:
        assert os.access(script, os.X_OK)
        subprocess.run(["bash", "-n", str(script)], check=True)

    backup = scripts[0].read_text()
    restore = scripts[1].read_text()
    verifier = (ROOT / "scripts/verify_artifacts.py").read_text()
    assert "var/backups" in backup
    assert "realpath" in backup
    assert "backup root must be beneath" in backup
    assert "manifest.json" in backup
    assert "umask 077" in backup
    assert "pg_dump" in backup
    assert "mysqldump --single-transaction" in backup
    assert "MYSQL_PWD" in backup
    assert "--password=" not in backup
    assert "database_dialect" in backup
    assert "version:2" in backup
    assert "--archive is required" in restore
    assert "--confirm-restore RESTORE is required" in restore
    assert 'case "$database_dialect" in' in restore
    assert "pg_restore" in restore
    assert "MYSQL_PWD" in restore
    assert "--password=" not in restore
    assert "database_dialect" in restore
    assert "tradingng-codex-gateway" not in backup
    assert "tradingng-codex-gateway" not in restore
    assert "tradingng-platform-caddy" not in restore
    assert "archive manifest is missing" in restore
    assert "--database-url-env" in verifier


def test_verify_only_rejects_backup_root_escape(tmp_path):
    result = subprocess.run(
        [
            str(ROOT / "scripts/backup_platform.sh"),
            "--verify-only",
            "--backup-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "backup root must be beneath" in result.stderr
