"""Installer rejection paths are tested before any dependency/system mutation."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy/bootstrap.sh"

def run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], text=True, capture_output=True)

def test_installer_help_has_no_side_effects():
    r = run("--help")
    assert r.returncode == 0
    assert "--domain" in r.stdout and "MT5/Wine" in r.stdout

def test_installer_rejects_invalid_hostname_before_installing():
    for domain in ("https://example.com", "example.com/path", "example.com:8443", "$(touch /tmp/bad)", "-bad.example.com"):
        r = run("--domain", domain)
        assert r.returncode != 0
        assert "DNS hostname" in r.stderr
        assert "Installing panel" not in r.stdout

def test_installer_rejects_invalid_repo_branch_and_traversal():
    r = run("--domain", "trade.example.com", "--repo", "user/repo;touch /tmp/bad")
    assert "Repository must" in r.stderr
    r = run("--domain", "trade.example.com", "--branch", "--evil")
    assert "Invalid branch" in r.stderr
    r = run("--domain", "trade.example.com", "--dir", "/opt/../root")
    assert "Installation directory" in r.stderr

def test_installer_never_overwrites_existing_directory(tmp_path):
    if os.geteuid() != 0:
        return
    sentinel = tmp_path / "existing.txt"
    sentinel.write_text("keep me")
    r = run("--domain", "trade.example.com", "--dir", str(tmp_path))
    assert r.returncode != 0 and "Directory already exists" in r.stderr
    assert sentinel.read_text() == "keep me"
    assert "Installing panel" not in r.stdout

def test_update_rejects_directory_without_checkout(tmp_path):
    deploy = tmp_path / "deploy"; deploy.mkdir()
    script = deploy / "update.sh"
    script.write_text((ROOT / "deploy/update.sh").read_text())
    r = subprocess.run(["bash", str(script)], text=True, capture_output=True)
    assert r.returncode != 0 and "installed Git checkout" in r.stderr
