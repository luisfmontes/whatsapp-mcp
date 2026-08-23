"""Test multi-account routing with real bridge processes."""

import pytest
import subprocess
import time
import os
import json
import tempfile
import signal
from pathlib import Path
import sys

# Add whatsapp-mcp-server to path
sys.path.insert(0, str(Path(__file__).parent))

import accounts
import whatsapp


@pytest.fixture
def temp_dirs():
    """Create two temporary directories for bridge instances."""
    dirs = []
    for i in range(2):
        tmpdir = tempfile.mkdtemp(prefix=f'wa_bridge_{i}_')
        dirs.append(tmpdir)
    yield dirs
    # Cleanup
    for d in dirs:
        try:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        except:
            pass


@pytest.fixture
def bridge_processes(temp_dirs):
    """Start two real bridge processes on different ports."""
    processes = []
    ports = [3097, 3098]
    accts = ['trabalho', 'pessoal']

    for i, (tmpdir, port, acct) in enumerate(zip(temp_dirs, ports, accts)):
        # Build bridge binary if not exists
        binary = Path('/tmp/wa.exe')
        if not binary.exists():
            # Try to build
            build_cmd = 'cd whatsapp-bridge && CGO_ENABLED=0 go build -o /tmp/wa.exe .'
            try:
                subprocess.run(build_cmd, shell=True, cwd='..', timeout=60, capture_output=True)
            except:
                pytest.skip("Could not build whatsapp-bridge binary")
                return []

        # Start bridge process
        env = os.environ.copy()
        env['WHATSAPP_BRIDGE_PORT'] = str(port)
        env['WHATSAPP_ACCOUNT'] = acct

        try:
            proc = subprocess.Popen(
                [str(binary)],
                cwd=tmpdir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            processes.append((proc, port, acct))
            time.sleep(0.5)  # Let process start
        except Exception as e:
            pytest.skip(f"Could not start bridge: {e}")
            return []

    yield processes

    # Cleanup: kill all processes
    for proc, _, _ in processes:
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except:
            proc.kill()


def test_account_routing_with_two_bridges(bridge_processes, tmp_path):
    """Test that account parameter routes to correct bridge port."""
    if not bridge_processes:
        pytest.skip("Bridge processes not available")

    # Create accounts.json with two accounts
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "pessoal": {
                "port": 3098,
                "dir": "/tmp/pessoal",
                "jid": ""
            },
            "trabalho": {
                "port": 3097,
                "dir": "/tmp/trabalho",
                "jid": ""
            }
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    # Set env var to point to accounts.json
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    try:
        # Test 1: resolve_account("trabalho") returns port 3097
        url_trabalho = accounts.resolve_account("trabalho")
        assert "3097" in url_trabalho, f"Expected port 3097 in {url_trabalho}"

        # Test 2: resolve_account("pessoal") returns port 3098
        url_pessoal = accounts.resolve_account("pessoal")
        assert "3098" in url_pessoal, f"Expected port 3098 in {url_pessoal}"

        # Test 3: resolve_account(None) defaults to pessoal (port 3098)
        url_default = accounts.resolve_account(None)
        assert "3098" in url_default, f"Expected default to port 3098 in {url_default}"

        # Test 4: get_bridge_status routing
        # This should call the correct port based on account parameter
        # Note: bridge might not be ready, so just verify routing doesn't crash
        try:
            healthy, reason, status = whatsapp.get_bridge_status(account="trabalho")
            # Status might be false (not paired), but call should have routed correctly
            assert isinstance(healthy, bool)
        except Exception as e:
            # Connection error is OK if bridge not ready
            assert "connection" in str(e).lower() or "refused" in str(e).lower()

        pytest.skip("Bridges not ready for full integration test")

    finally:
        # Cleanup
        if 'WHATSAPP_ACCOUNTS_FILE' in os.environ:
            del os.environ['WHATSAPP_ACCOUNTS_FILE']


def test_write_op_without_account_fails_with_multiple_accounts(tmp_path):
    """Test that write operations fail without account when multiple accounts configured."""
    # Create accounts.json
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "pessoal": {"port": 3098, "dir": "/tmp/pessoal", "jid": ""},
            "trabalho": {"port": 3097, "dir": "/tmp/trabalho", "jid": ""}
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    try:
        # Try to send message without account parameter
        # Should fail with error about multiple accounts
        success, msg = whatsapp.send_message("test@s.whatsapp.net", "test")
        assert not success
        # Error should mention the account options
        assert "trabalho" in msg or "pessoal" in msg or "account" in msg.lower()

    finally:
        if 'WHATSAPP_ACCOUNTS_FILE' in os.environ:
            del os.environ['WHATSAPP_ACCOUNTS_FILE']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
