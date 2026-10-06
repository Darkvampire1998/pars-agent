"""Installer rejection paths are tested before any dependency/system mutation."""
import os
from pathlib import Path
import subprocess
import base64
import importlib.util
import pytest
from cryptography.fernet import Fernet

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


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'deploy' / (name + '.py'))
    out = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(out)
    return out


@pytest.mark.parametrize('args', [('--ip', '999.1.1.1'), ('--ip', '127.0.0.1'),
    ('--ip', '192.168.1.2'), ('--ip', '224.0.0.1'), ('--ip', 'https://8.8.8.8'),
    ('--ip', '8.8.8.8:8443'), ('--ip', '$(touch /tmp/bad)'),
    ('--domain', 'trade.example.com', '--ip', '8.8.8.8'),
    ('--ip', '8.8.8.8', '--port', '80'), ('--ip', '8.8.8.8', '--port', '65536')])
def test_ip_installer_rejects_bad_input_before_dependencies(args):
    r = run(*args)
    assert r.returncode != 0
    assert 'Installing panel' not in r.stdout


def test_resume_rejects_unrelated_checkout_before_dependencies(tmp_path):
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'remote', 'add', 'origin', 'https://github.com/other/project.git'], check=True)
    r = run('--ip', '8.8.8.8', '--dir', str(tmp_path), '--resume')
    assert r.returncode != 0 and 'different origin' in r.stderr
    assert 'Installing panel' not in r.stdout


def test_ip_endpoint_and_secure_configuration(tmp_path):
    configure = module('configure')
    values = configure.endpoint(ip='8.8.8.8', port=8443)
    assert values['PUBLIC_URL'] == 'https://8.8.8.8:8443'
    assert values['COOKIE_SECURE'] == 'true'
    assert values['CADDY_CONFIG'] == './deploy/Caddyfile.ip'
    assert configure.endpoint(ip='2606:4700:4700::1111')['PUBLIC_URL'] == 'https://[2606:4700:4700::1111]'
    path = tmp_path / '.env'
    configure.write_config(path, values, host_build=True)
    contents = path.read_text()
    assert 'COMPOSE_FILE=compose.yaml:deploy/compose.host-build.yaml' in contents
    assert 'LIVE_TRADING_ALLOWED=false' in contents
    assert path.stat().st_mode & 0o777 == 0o600
    key = next(line.split('=', 1)[1] for line in contents.splitlines() if line.startswith('ENCRYPTION_KEY='))
    assert len(base64.urlsafe_b64decode(key)) == 32


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '203.0.113.10', '224.1.2.3', '::1', 'fd00::1', 'https://8.8.8.8'])
def test_configuration_rejects_nonpublic_or_invalid_ip(ip):
    with pytest.raises(ValueError):
        module('configure').endpoint(ip=ip)


def test_address_migration_preserves_encrypted_data_and_policies(tmp_path):
    configure = module('configure')
    path = tmp_path / '.env'
    key = Fernet.generate_key().decode()
    token = Fernet(key.encode()).encrypt(b'persisted Telegram token')
    path.write_text(f'# operator settings\nDOMAIN=trade.example.com\nPUBLIC_URL=https://trade.example.com\nENCRYPTION_KEY={key}\nREGISTRATION_ENABLED=false\nLIVE_TRADING_ALLOWED=true\nCUSTOM_SETTING=keep\n')
    configure.write_config(path, configure.endpoint(ip='8.8.8.8', port=8443), reconfigure=True)
    contents = path.read_text()
    assert f'ENCRYPTION_KEY={key}\n' in contents
    assert Fernet(key.encode()).decrypt(token) == b'persisted Telegram token'
    assert '# operator settings' in contents and 'CUSTOM_SETTING=keep' in contents
    assert 'REGISTRATION_ENABLED=false' in contents and 'LIVE_TRADING_ALLOWED=true' in contents
    assert 'COOKIE_SECURE=true' in contents
    assert path.stat().st_mode & 0o777 == 0o600


def test_reconfigure_needs_explicit_address_change_and_valid_key(tmp_path):
    configure = module('configure')
    path = tmp_path / '.env'
    values = configure.endpoint(domain='trade.example.com')
    configure.write_config(path, values)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        configure.write_config(path, values)
    with pytest.raises(ValueError, match='different address'):
        configure.write_config(path, configure.endpoint(ip='8.8.8.8'), reuse=True)
    assert path.read_bytes() == before
    configure.write_config(path, values, reuse=True)
    assert path.read_bytes() == before
    path.write_text('ENCRYPTION_KEY=corrupt\n')
    with pytest.raises(ValueError, match='invalid'):
        configure.write_config(path, values, reconfigure=True)
    assert path.read_text() == 'ENCRYPTION_KEY=corrupt\n'


@pytest.mark.parametrize('build_result,tls_result,expected', [(1, 0, 'Installation did not complete'),
    (0, 1, 'HTTPS is not verified yet'), (0, 0, 'Panel is responding over verified HTTPS')])
def test_install_reports_success_only_after_verified_endpoint(tmp_path, build_result, tls_result, expected):
    deploy = tmp_path / 'deploy'; deploy.mkdir()
    for name in ('configure.py', 'install.sh'):
        (deploy / name).write_bytes((ROOT / 'deploy' / name).read_bytes())
    (deploy / 'check-network.py').write_text('print("DNS/TLS preflight passed")\n')
    tools_dir = tmp_path / 'tools'; tools_dir.mkdir()
    (tools_dir / 'docker').write_text(f'#!/bin/sh\nif [ "$2" = up ]; then exit {build_result}; fi\nexit 0\n')
    (tools_dir / 'curl').write_text(f'#!/bin/sh\nexit {tls_result}\n')
    for file in tools_dir.iterdir():
        file.chmod(0o755)
    r = subprocess.run(['bash', str(deploy / 'install.sh'), '--ip', '8.8.8.8', '--port', '8443'],
                       text=True, capture_output=True, env={**os.environ, 'PATH': str(tools_dir) + ':' + os.environ['PATH']})
    assert expected in r.stdout + r.stderr
    assert (r.returncode == 0) == (build_result == tls_result == 0)
    if r.returncode:
        assert 'Panel is responding over verified HTTPS' not in r.stdout
    else:
        assert 'https://8.8.8.8:8443' in r.stdout


def test_network_preflight_distinguishes_dns_failure_from_http_error(monkeypatch):
    network = module('check-network')
    monkeypatch.setattr(network.socket, 'getaddrinfo', lambda *a, **kw: [])
    def http_error(*a, **kw):
        raise network.urllib.error.HTTPError('https://files.pythonhosted.org/', 404, 'missing', {}, None)
    monkeypatch.setattr(network.urllib.request, 'urlopen', http_error)
    assert network.main() == 0
    def dns_error(*a, **kw):
        raise OSError('Temporary failure in name resolution')
    monkeypatch.setattr(network.socket, 'getaddrinfo', dns_error)
    assert network.main() == 1
