"""Post-build sanity checks for the installer pipeline.

These tests assert the contract that build.ps1 (invoked via compile.bat) must
satisfy. They are skipped when dist/ has not been populated yet, so a fresh
checkout can still run `pytest` without forcing an installer build first.

The build itself happens in PowerShell and is too expensive to invoke per-test;
treat these as gates that run after a build, locally or in CI."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"
INSTALLER_ISS = REPO_ROOT / "installer" / "maxima-ocr.iss"

INSTALLER_GLOB = "MaximaOCR-Setup-*.exe"
INSTALLER_NAME_RE = re.compile(r"^MaximaOCR-Setup-(.+)\.exe$")


def _build_ps1_has_run() -> bool:
    """True if build.ps1 has produced its outputs.

    Uses dist/tools/modbus_client.exe as the marker because the legacy
    compile.bat did not produce it -- so its presence reliably means the
    current build pipeline has run, not an older one."""
    return (DIST / "tools" / "modbus_client.exe").is_file()


pytestmark = pytest.mark.skipif(
    not _build_ps1_has_run(),
    reason="build.ps1 has not been run; run compile.bat to enable these tests",
)


def test_modbus_server_exe_built():
    exe = DIST / "modbus_server.exe"
    assert exe.is_file()
    # PyInstaller onefile output is always at least a few MB; reject empty/stub files.
    assert exe.stat().st_size > 1_000_000, f"{exe} suspiciously small"


def test_config_yaml_staged_next_to_exe():
    cfg = DIST / "config.yaml"
    assert cfg.is_file(), "build.ps1 must stage config.yaml next to modbus_server.exe"


def test_modbus_client_exe_built():
    exe = DIST / "tools" / "modbus_client.exe"
    assert exe.is_file(), "tools/modbus_client.exe must be compiled into dist/tools/"
    assert exe.stat().st_size > 500_000, f"{exe} suspiciously small"


def test_installer_exe_built():
    """build.ps1 must produce exactly one MaximaOCR-Setup-<version>.exe."""
    candidates = list(DIST.glob(INSTALLER_GLOB))
    assert candidates, (
        f"No installer found in {DIST} matching '{INSTALLER_GLOB}'. "
        "build.ps1 must produce one per build."
    )
    # The installer bundles modbus_server.exe + the full models bundle, so even
    # with aggressive lzma2 compression it should be at least several MB.
    for installer in candidates:
        assert installer.stat().st_size > 5_000_000, f"{installer} suspiciously small"


def test_installer_filename_encodes_a_valid_version():
    """The version embedded in the installer filename should be a recognized
    version string -- catches accidental passes of empty / shell-broken values."""
    candidates = list(DIST.glob(INSTALLER_GLOB))
    assert candidates, "no installer to inspect; covered by test_installer_exe_built"
    version_re = re.compile(r"^[0-9]+(\.[0-9]+){1,2}(-[A-Za-z0-9.]+)?$")
    for installer in candidates:
        m = INSTALLER_NAME_RE.match(installer.name)
        assert m, f"installer name doesn't match expected pattern: {installer.name}"
        version = m.group(1)
        assert version_re.fullmatch(version), (
            f"installer '{installer.name}' encodes version '{version}', "
            "which is not a recognized version string"
        )


def test_installer_script_references_real_sources():
    """Spec-as-test: every Source: in maxima-ocr.iss must point at a file that
    actually exists. Run after a successful build, so build/tools/ entries
    (nssm.exe, etc.) are populated."""
    text = INSTALLER_ISS.read_text(encoding="utf-8")
    src_pattern = re.compile(r'^\s*Source:\s*"([^"]+)"', re.MULTILINE)
    iss_dir = INSTALLER_ISS.parent
    missing = []
    for raw in src_pattern.findall(text):
        # Glob entries like models\* -- check the parent directory exists.
        rel = raw
        if rel.endswith("*"):
            rel = rel.rstrip("*").rstrip("\\")
        candidate = (iss_dir / rel).resolve()
        if not candidate.exists():
            missing.append(raw)
    assert not missing, f"installer/maxima-ocr.iss references missing files: {missing}"
