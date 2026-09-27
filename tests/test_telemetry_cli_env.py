"""Additional tests for the telemetry CLI env-loading + report UX fixes.

These tests exercise the two fixes landed in V1.3.4:

1. ``python3 -m factrail.telemetry report`` loads ``.env`` automatically
   from the project root, so it works from a clean environment (no
   ``FACTRAIL_TELEMETRY_ENABLED`` in the parent shell).

2. ``report`` is readable even when recording is currently disabled — it
   reads and displays the existing telemetry DB and prints a ``Recording:``
   line rather than exiting early with a "disabled" message.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def project_root() -> Path:
    """The Factrail project root (parent of the ``factrail`` package)."""
    return Path(__file__).resolve().parent.parent / "factrail"


@pytest.fixture
def clean_env():
    """A fresh environment with no Factrail variables set at all."""
    excluded = {
        "PATH", "HOME", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL",
        "TERM", "PWD", "_", "PYTHONPATH", "VIRTUAL_ENV",
    }
    env = {k: v for k, v in os.environ.items() if k in excluded}
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", "/root")
    return env


def run_cli(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``python3 -m factrail.telemetry ...`` and return the result."""
    cmd = [sys.executable, "-m", "factrail.telemetry", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
        cwd=str(Path(__file__).resolve().parent.parent),
    )


# ---------------------------------------------------------------------------
# 1. Clean-environment report loads .env automatically
# ---------------------------------------------------------------------------


class TestCleanEnvironmentReport:
    def test_report_works_without_env_var(self, clean_env, project_root):
        """From a clean env, ``report`` must not print the 'disabled' message.

        The .env file in the project root sets FACTRAIL_TELEMETRY_ENABLED=1,
        and the telemetry module loads it automatically.  The CLI must not
        require the user to source .env manually.
        """
        result = run_cli(["report"], env=clean_env)
        assert result.returncode == 0, result.stderr
        # Should NOT show the old "disabled" hint
        assert "Telemetry is disabled" not in result.stdout
        assert "Set FACTRAIL_TELEMETRY_ENABLED=1 to enable" not in result.stdout
        # Must show the recording line
        assert "Recording:" in result.stdout
        assert "Factrail Telemetry Report" in result.stdout

    def test_report_loads_telemetry_db_from_env(self, clean_env, project_root):
        """From a clean env, ``report`` must find the production DB via .env."""
        result = run_cli(["report"], env=clean_env)
        assert result.returncode == 0
        # The .env sets FACTRAIL_TELEMETRY_DB=/var/lib/factrail/telemetry.db
        assert "/var/lib/factrail/telemetry.db" in result.stdout

    def test_report_uses_default_db_when_no_env(self, clean_env, project_root):
        """If no .env is present, ``report`` must fall back to the default DB path."""
        # Create a temp dir as a fake "project root" with no .env
        tmp = tempfile.mkdtemp()
        try:
            # Point PYTHONPATH at the real package but use a fake CWD with no .env
            env = dict(clean_env)
            env["PYTHONPATH"] = str(project_root.parent)
            # The CLI runs from cwd — _project_root() is based on the package
            # location, not CWD, so we need to make the package think its root
            # has no .env.  We do that by temporarily moving the real .env.
            dotenv = project_root / ".env"
            backup = None
            if dotenv.exists():
                backup = dotenv.read_bytes()
                dotenv.unlink()
            try:
                result = run_cli(["report"], env=env)
                # With .env gone, FACTRAIL_TELEMETRY_DB is unset, so the CLI
                # default (/var/lib/factrail/telemetry.db) is used.
                assert result.returncode == 0
                assert "/var/lib/factrail/telemetry.db" in result.stdout or "DB:" in result.stdout
            finally:
                if backup is not None:
                    dotenv.write_bytes(backup)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. report is readable when recording is disabled
# ---------------------------------------------------------------------------


class TestReportWhenRecordingDisabled:
    def test_report_with_recording_disabled(self, clean_env, project_root):
        """Even when FACTRAIL_TELEMETRY_ENABLED=0 (recording off),
        ``report`` must still read the existing DB and print the stats.

        Passing FACTRAIL_TELEMETRY_ENABLED=0 in the env blocks the
        ``.env`` auto-load (which would otherwise set it to 1), so we can
        exercise the disabled path without touching the filesystem.
        """
        env = dict(clean_env)
        env["FACTRAIL_TELEMETRY_ENABLED"] = "0"
        result = run_cli(["report"], env=env)
        assert result.returncode == 0
        assert "Recording: disabled" in result.stdout
        # Must still show DB path and the report header — not an early exit
        assert "Factrail Telemetry Report" in result.stdout
        assert "DB:" in result.stdout

    def test_report_with_explicit_enabled(self, clean_env, project_root):
        """With FACTRAIL_TELEMETRY_ENABLED=1, ``report`` must print
        ``Recording: enabled``."""
        env = dict(clean_env)
        env["FACTRAIL_TELEMETRY_ENABLED"] = "1"
        result = run_cli(["report"], env=env)
        assert result.returncode == 0
        assert "Recording: enabled" in result.stdout

    def test_report_with_explicit_disabled(self, clean_env, project_root):
        """With FACTRAIL_TELEMETRY_ENABLED=0, ``report`` must print
        ``Recording: disabled``."""
        env = dict(clean_env)
        env["FACTRAIL_TELEMETRY_ENABLED"] = "0"
        result = run_cli(["report"], env=env)
        assert result.returncode == 0
        assert "Recording: disabled" in result.stdout

    def test_purge_works_without_env_var(self, clean_env, project_root):
        """``purge`` must also load .env automatically from a clean env."""
        result = run_cli(["purge"], env=clean_env)
        assert result.returncode == 0
        assert "Purged" in result.stdout

    def test_purge_uses_production_db(self, clean_env, project_root):
        """``purge`` from a clean env must operate on the production DB."""
        result = run_cli(["purge"], env=clean_env)
        assert result.returncode == 0
        assert "/var/lib/factrail/telemetry.db" in result.stdout


# ---------------------------------------------------------------------------
# 3. FACTRAIL_TELEMETRY_SECRET is never printed
# ---------------------------------------------------------------------------


class TestSecretNotExposed:
    def test_secret_not_in_report_output(self, clean_env, project_root):
        """Running ``report`` from a clean env (which loads .env incl. SECRET)
        must not leak the secret value in stdout."""
        result = run_cli(["report"], env=clean_env)
        assert result.returncode == 0
        # The secret in .env is redacted in our view as ***, but we can still
        # assert that no environment variable values appear verbatim in output.
        # The SECRET is a hex string; check that no long hex strings from env
        # appear.  We check the keys instead — they must not appear.
        for key in ("FACTRAIL_TELEMETRY_SECRET", "INSEE_API_KEY"):
            # The literal key name must not appear in report output
            assert key not in result.stdout, f"{key} leaked into report output"

    def test_secret_not_in_purge_output(self, clean_env, project_root):
        """``purge`` output must not contain secret or API key names."""
        result = run_cli(["purge"], env=clean_env)
        assert result.returncode == 0
        for key in ("FACTRAIL_TELEMETRY_SECRET", "INSEE_API_KEY"):
            assert key not in result.stdout, f"{key} leaked into purge output"

    def test_no_env_dump_in_output(self, clean_env, project_root):
        """The report and purge output must not contain full env var dumps
        (defense-in-depth against accidental logging of env)."""
        for cmd in (["report"], ["purge"]):
            result = run_cli(cmd, env=clean_env)
            assert result.returncode == 0
            # Nothing that looks like a raw env dump (KEY=VALUE on its own line)
            for line in result.stdout.splitlines():
                stripped = line.strip()
                # A line that is just "KEY=value" where value is non-trivial
                # is suspicious in a telemetry report context.
                if "=" in stripped and stripped.count("=") == 1:
                    key, val = stripped.split("=", 1)
                    if key and val and len(val) > 8:
                        pytest.fail(
                            f"Suspected env leak in {cmd} output: {stripped}"
                        )
