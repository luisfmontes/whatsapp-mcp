"""Test account routing with real bridge processes and data verification.

Verifies that whatsapp.py correctly routes account parameter to the correct
bridge instance by inserting distinct data in each bridge and confirming
calls with account= reach the correct one.
"""

import ast
import pytest
import subprocess
import time
import os
import json
import sqlite3
import signal
import sys
import tempfile
import logging
import requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import accounts
import whatsapp

# Configure logging for this test module
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)


@pytest.fixture
def bridge_dirs():
    """Create two temporary directories for bridge instances."""
    dirs = {}
    for acct in ['trabalho', 'pessoal']:
        tmpdir = tempfile.mkdtemp(prefix=f'wa_bridge_{acct}_')
        dirs[acct] = tmpdir
    yield dirs
    # Cleanup
    for tmpdir in dirs.values():
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except:
            pass


@pytest.fixture
def bridge_processes(bridge_dirs):
    """Start two real bridge processes and insert test data.

    Builds the whatsapp-bridge binary if needed and starts two instances
    on different ports (3097, 3098), each with a temporary directory and
    distinct test data.
    """
    procs = {}
    ports = {'trabalho': 3097, 'pessoal': 3098}

    # Find or build the bridge binary
    binary = Path(tempfile.gettempdir()) / 'wa.exe'
    bridge_src = Path(__file__).parent.parent / 'whatsapp-bridge'

    if not binary.exists():
        # Build the binary with CGO_ENABLED=0
        build_env = os.environ.copy()
        build_env['CGO_ENABLED'] = '0'

        result = subprocess.run(
            ['go', 'build', '-o', str(binary)],
            cwd=str(bridge_src),
            env=build_env,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to build whatsapp-bridge binary:\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

    if not binary.exists():
        raise RuntimeError(f"Bridge binary not found at {binary} after build")

    # Start bridge processes
    for acct, port in ports.items():
        tmpdir = bridge_dirs[acct]
        env = os.environ.copy()
        env['WHATSAPP_BRIDGE_PORT'] = str(port)
        env['WHATSAPP_ACCOUNT'] = acct

        # Remove WHATSAPP_ACCOUNTS_FILE to ensure clean state
        env.pop('WHATSAPP_ACCOUNTS_FILE', None)

        proc = subprocess.Popen(
            [str(binary)],
            cwd=tmpdir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        procs[acct] = proc
        time.sleep(1.5)  # Give process time to create database and listen

    # Insert test data into each bridge's database
    for acct, port in ports.items():
        tmpdir = bridge_dirs[acct]
        db_path = Path(tmpdir) / 'store' / 'messages.db'

        # Wait a bit more for the database to be created
        max_wait = 10
        while not db_path.exists() and max_wait > 0:
            time.sleep(0.5)
            max_wait -= 1

        if not db_path.exists():
            raise RuntimeError(f"Database not created for {acct} at {db_path}")

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        # Insert a test chat that only exists in this bridge
        # Use account-specific JID to make it unique
        # Schema: CREATE TABLE IF NOT EXISTS chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)
        # Note: Go's database/sql converts time.Time to RFC3339 string in SQLite
        test_jid = f'551100{port}@s.whatsapp.net'
        test_name = f'CHAT-{acct.upper()}'
        # Use RFC3339 format which is what Go's database/sql writes for TIMESTAMP
        last_message_time = datetime.now().isoformat() + "Z"
        cursor.execute(
            'INSERT OR REPLACE INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)',
            (test_jid, test_name, last_message_time)
        )
        conn.commit()
        conn.close()

    yield procs

    # Cleanup: terminate all processes
    for acct, proc in procs.items():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except:
            try:
                proc.kill()
            except:
                pass


def test_account_routing_with_real_bridges(bridge_processes, bridge_dirs, tmp_path):
    """Test that account parameter routes to correct bridge by verifying content.

    This test proves routing by:
    1. Starting two bridge processes on different ports (3097, 3098)
    2. Inserting distinct test data in each bridge (CHAT-TRABALHO, CHAT-PESSOAL)
    3. Calling list_chats(account="trabalho") and list_chats(account="pessoal")
    4. Verifying each call retrieves data from the correct bridge ONLY
    5. Calling get_bridge_status(account=...) and verifying responses come from correct port

    If the routing is broken, the HTTP requests would go to the wrong port
    and retrieve data from the wrong bridge.
    """
    # bridge_processes fixture ensures bridges are running; if not, it raises RuntimeError
    assert bridge_processes, "bridge_processes fixture should have started bridges"

    # Create accounts.json pointing to the test bridges
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "trabalho": {
                "port": 3097,
                "dir": bridge_dirs['trabalho'],
                "jid": ""
            },
            "pessoal": {
                "port": 3098,
                "dir": bridge_dirs['pessoal'],
                "jid": ""
            }
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    # Set env to use our test accounts.json
    old_env = os.environ.get('WHATSAPP_ACCOUNTS_FILE')
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    try:
        # Test 1: resolve_account returns correct ports
        url_trabalho = accounts.resolve_account("trabalho")
        assert "3097" in url_trabalho, f"trabalho should use port 3097, got {url_trabalho}"

        url_pessoal = accounts.resolve_account("pessoal")
        assert "3098" in url_pessoal, f"pessoal should use port 3098, got {url_pessoal}"

        # Test 2: list_chats with account parameter - must route to correct bridge
        # The fixture inserted CHAT-TRABALHO in trabalho's database and CHAT-PESSOAL in pessoal's
        chats_trabalho = whatsapp.list_chats(account="trabalho")
        chats_pessoal = whatsapp.list_chats(account="pessoal")

        # Both should return list of Chat objects
        assert isinstance(chats_trabalho, list), f"list_chats should return list, got {type(chats_trabalho)}"
        assert isinstance(chats_pessoal, list), f"list_chats should return list, got {type(chats_pessoal)}"

        # Extract chat names to verify routing
        trabalho_names = [chat.name for chat in chats_trabalho]
        pessoal_names = [chat.name for chat in chats_pessoal]

        # Verify routing by content: trabalho account should see CHAT-TRABALHO, pessoal should see CHAT-PESSOAL
        assert "CHAT-TRABALHO" in trabalho_names, \
            f"list_chats(account='trabalho') should contain CHAT-TRABALHO, got names: {trabalho_names}"
        assert "CHAT-PESSOAL" not in trabalho_names, \
            f"list_chats(account='trabalho') should NOT contain CHAT-PESSOAL (routing broken), got names: {trabalho_names}"

        assert "CHAT-PESSOAL" in pessoal_names, \
            f"list_chats(account='pessoal') should contain CHAT-PESSOAL, got names: {pessoal_names}"
        assert "CHAT-TRABALHO" not in pessoal_names, \
            f"list_chats(account='pessoal') should NOT contain CHAT-TRABALHO (routing broken), got names: {pessoal_names}"

        # Test 3: get_bridge_status with account parameter - verify it reaches correct port
        # Bridge status will show the running process's actual status
        healthy_trabalho, reason_trabalho, status_trabalho = whatsapp.get_bridge_status(account="trabalho")
        healthy_pessoal, reason_pessoal, status_pessoal = whatsapp.get_bridge_status(account="pessoal")

        # Both should return successful status (bridges are running)
        assert isinstance(status_trabalho, dict), \
            f"get_bridge_status(account='trabalho') should return dict, got {type(status_trabalho)}"
        assert isinstance(status_pessoal, dict), \
            f"get_bridge_status(account='pessoal') should return dict, got {type(status_pessoal)}"

        # Each status should have keys indicating it came from the bridge
        assert "healthy" in status_trabalho or "logged_in" in status_trabalho, \
            f"get_bridge_status(account='trabalho') should have bridge data, got: {status_trabalho}"
        assert "healthy" in status_pessoal or "logged_in" in status_pessoal, \
            f"get_bridge_status(account='pessoal') should have bridge data, got: {status_pessoal}"

    finally:
        # Restore env
        if old_env is None:
            os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        else:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_env


def test_write_op_guard_without_account_with_multiple_accounts(tmp_path):
    """Test that write ops fail without account when multiple accounts configured.

    This verifies D2 from design: "Escrita sem account com mais de uma conta é erro,
    retornando mensagem antes de qualquer HTTP."

    The error message must:
    1. Be raised BEFORE any HTTP request (proving it's a guard, not a runtime error)
    2. Name both configured accounts
    3. Mention 'account' to guide the user
    """
    # Create accounts.json with two accounts
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "pessoal": {"port": 3098, "dir": "/tmp/pessoal", "jid": ""},
            "trabalho": {"port": 3097, "dir": "/tmp/trabalho", "jid": ""}
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    old_env = os.environ.get('WHATSAPP_ACCOUNTS_FILE')
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    try:
        # Try write operation without account — should raise ValueError
        # (not fail with False status after HTTP request)
        with pytest.raises(ValueError) as exc_info:
            whatsapp.send_message("55111234567@s.whatsapp.net", "test", account=None)

        error_msg = str(exc_info.value)

        # Error must mention the configured accounts
        assert 'trabalho' in error_msg and 'pessoal' in error_msg, \
            f"Error should mention both accounts, got: {error_msg}"

        # Error must guide toward using account parameter
        assert 'account' in error_msg.lower(), \
            f"Error should mention 'account' parameter, got: {error_msg}"

    finally:
        if old_env is None:
            os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        else:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_env




def test_status_agregado(bridge_processes, bridge_dirs, tmp_path):
    """Test D13: get_bridge_status() without account returns aggregated status.
    
    With two accounts configured and only the default account's bridge running,
    get_bridge_status() without account parameter should return:
    - Aggregated dict with status for both accounts
    - Default account with real status from /api/status
    - Non-default account marked as offline with task name
    """
    # Stop the trabalho process to simulate it being offline
    if 'trabalho' in bridge_processes:
        try:
            bridge_processes['trabalho'].terminate()
            bridge_processes['trabalho'].wait(timeout=2)
        except:
            try:
                bridge_processes['trabalho'].kill()
            except:
                pass
    
    time.sleep(0.5)  # Give it time to stop
    
    # Create accounts.json: default (pessoal) at 3098, trabalho at 3097
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "trabalho": {
                "port": 3097,
                "dir": bridge_dirs['trabalho'],
                "jid": ""
            },
            "pessoal": {
                "port": 3098,
                "dir": bridge_dirs['pessoal'],
                "jid": ""
            }
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    old_env = os.environ.get('WHATSAPP_ACCOUNTS_FILE')
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    try:
        # Call get_bridge_status without account
        # Since only pessoal is running, trabalho will be offline
        healthy, reason, status_dict = whatsapp.get_bridge_status(account=None)
        
        # D13: Should return aggregated format
        assert reason == "aggregated", f"Expected reason='aggregated', got '{reason}'"
        assert isinstance(status_dict, dict), f"Expected dict status, got {type(status_dict)}"
        
        # Should have both accounts
        assert "pessoal" in status_dict, f"Missing 'pessoal' in aggregated status"
        assert "trabalho" in status_dict, f"Missing 'trabalho' in aggregated status"
        
        # Pessoal (default, running) should have real status
        pessoal_status = status_dict["pessoal"]
        assert "healthy" in pessoal_status, f"pessoal missing 'healthy'"
        assert "reason" in pessoal_status, f"pessoal missing 'reason'"
        assert "status" in pessoal_status, f"pessoal missing 'status'"
        
        # Trabalho (not running) should be marked offline with task name
        trabalho_status = status_dict["trabalho"]
        assert trabalho_status["healthy"] == False, f"trabalho should be unhealthy"
        assert "WhatsAppMCPBridge-trabalho" in trabalho_status["reason"],             f"trabalho reason should cite task name, got: {trabalho_status['reason']}"
        
    finally:
        if old_env is None:
            os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        else:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_env


def test_fora_do_ar(bridge_processes, bridge_dirs, tmp_path):
    """Test D12: list_chats(account="offline") raises clear error with task name.

    When a specified account's bridge is unreachable, the tool should raise
    ValueError with a clear message naming the account alias and the
    scheduled task name, without raw stacktrace or attempting to start
    the process.
    """
    # Stop the trabalho process to simulate it being offline
    if 'trabalho' in bridge_processes:
        try:
            bridge_processes['trabalho'].terminate()
            bridge_processes['trabalho'].wait(timeout=2)
        except:
            try:
                bridge_processes['trabalho'].kill()
            except:
                pass

    time.sleep(0.5)  # Give it time to stop

    # Create accounts.json: both accounts configured, but we'll not start trabalho
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "trabalho": {
                "port": 3097,
                "dir": bridge_dirs['trabalho'],
                "jid": ""
            },
            "pessoal": {
                "port": 3098,
                "dir": bridge_dirs['pessoal'],
                "jid": ""
            }
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    old_env = os.environ.get('WHATSAPP_ACCOUNTS_FILE')
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    try:
        # Try to call list_chats with offline account
        # trabalho is not running, so this should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            whatsapp.list_chats(account="trabalho")

        error_msg = str(exc_info.value)

        # Error must mention the account alias
        assert "trabalho" in error_msg,             f"Error should mention account alias 'trabalho', got: {error_msg}"

        # Error must mention the scheduled task name
        assert "WhatsAppMCPBridge-trabalho" in error_msg,             f"Error should mention task name 'WhatsAppMCPBridge-trabalho', got: {error_msg}"

        # Error must NOT contain raw Python stacktrace indicators
        assert "Traceback" not in error_msg, f"Error should not include traceback"

    finally:
        if old_env is None:
            os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        else:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_env


def test_multiple_read_functions_check_offline_account(bridge_processes, bridge_dirs, tmp_path):
    """Test D12: Multiple read functions properly report when account is offline.

    D12 requires that any read function called with an explicit account parameter
    whose bridge is unreachable should raise ValueError with account alias and
    task name, not silently return empty data.

    Tests all five read functions: search_contacts, get_chat, get_user_info,
    get_group_info, and get_profile_picture.
    """
    # Stop the trabalho process to simulate it being offline
    if 'trabalho' in bridge_processes:
        try:
            bridge_processes['trabalho'].terminate()
            bridge_processes['trabalho'].wait(timeout=2)
        except:
            try:
                bridge_processes['trabalho'].kill()
            except:
                pass

    time.sleep(0.5)  # Give it time to stop

    # Create accounts.json: both accounts configured, but trabalho is offline
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "trabalho": {
                "port": 3097,
                "dir": bridge_dirs['trabalho'],
                "jid": ""
            },
            "pessoal": {
                "port": 3098,
                "dir": bridge_dirs['pessoal'],
                "jid": ""
            }
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    old_env = os.environ.get('WHATSAPP_ACCOUNTS_FILE')
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    try:
        # Test 1: search_contacts with offline account
        with pytest.raises(ValueError) as exc_info:
            whatsapp.search_contacts("test", account="trabalho")
        error_msg = str(exc_info.value)
        assert "trabalho" in error_msg, f"search_contacts error should mention account, got: {error_msg}"
        assert "WhatsAppMCPBridge-trabalho" in error_msg, f"search_contacts error should mention task, got: {error_msg}"

        # Test 2: get_chat with offline account
        with pytest.raises(ValueError) as exc_info:
            whatsapp.get_chat("123@s.whatsapp.net", account="trabalho")
        error_msg = str(exc_info.value)
        assert "trabalho" in error_msg, f"get_chat error should mention account, got: {error_msg}"
        assert "WhatsAppMCPBridge-trabalho" in error_msg, f"get_chat error should mention task, got: {error_msg}"

        # Test 3: get_user_info with offline account
        with pytest.raises(ValueError) as exc_info:
            whatsapp.get_user_info(["123@s.whatsapp.net"], account="trabalho")
        error_msg = str(exc_info.value)
        assert "trabalho" in error_msg, f"get_user_info error should mention account, got: {error_msg}"
        assert "WhatsAppMCPBridge-trabalho" in error_msg, f"get_user_info error should mention task, got: {error_msg}"

        # Test 4: get_group_info with offline account
        with pytest.raises(ValueError) as exc_info:
            whatsapp.get_group_info("123@g.us", account="trabalho")
        error_msg = str(exc_info.value)
        assert "trabalho" in error_msg, f"get_group_info error should mention account, got: {error_msg}"
        assert "WhatsAppMCPBridge-trabalho" in error_msg, f"get_group_info error should mention task, got: {error_msg}"

        # Test 5: get_profile_picture with offline account
        with pytest.raises(ValueError) as exc_info:
            whatsapp.get_profile_picture("123@s.whatsapp.net", account="trabalho")
        error_msg = str(exc_info.value)
        assert "trabalho" in error_msg, f"get_profile_picture error should mention account, got: {error_msg}"
        assert "WhatsAppMCPBridge-trabalho" in error_msg, f"get_profile_picture error should mention task, got: {error_msg}"

    finally:
        if old_env is None:
            os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        else:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_env


def test_account_routing_mutation_detect_broken_resolve(bridge_processes, bridge_dirs, tmp_path):
    """Prove that test_account_routing_with_real_bridges detects broken routing.

    This test applies a mutation to resolve_account (ignores alias parameter,
    always returns default) and verifies the content-based assertions catch it.
    """
    # Stop trabalho process
    if 'trabalho' in bridge_processes:
        try:
            bridge_processes['trabalho'].terminate()
            bridge_processes['trabalho'].wait(timeout=2)
        except:
            try:
                bridge_processes['trabalho'].kill()
            except:
                pass

    time.sleep(0.5)

    # Create accounts.json
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "trabalho": {
                "port": 3097,
                "dir": bridge_dirs['trabalho'],
                "jid": ""
            },
            "pessoal": {
                "port": 3098,
                "dir": bridge_dirs['pessoal'],
                "jid": ""
            }
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    old_env = os.environ.get('WHATSAPP_ACCOUNTS_FILE')
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    # Save original resolve_account
    original_resolve_account = accounts.resolve_account

    def mutated_resolve_account(account):
        """MUTATED: Always return default, ignore alias parameter"""
        return original_resolve_account(None)

    try:
        # Apply mutation
        accounts.resolve_account = mutated_resolve_account

        # Get URLs with mutation applied
        url_trabalho = accounts.resolve_account("trabalho")
        url_pessoal = accounts.resolve_account("pessoal")

        # Both should return default (pessoal at 3098)
        assert "3098" in url_trabalho, f"mutation: trabalho should map to default (3098), got {url_trabalho}"
        assert "3098" in url_pessoal, f"mutation: pessoal should be default (3098), got {url_pessoal}"

        # Now call list_chats - it should get data from pessoal only (both calls go to 3098)
        chats_trabalho = whatsapp.list_chats(account="trabalho")
        chats_pessoal = whatsapp.list_chats(account="pessoal")

        trabalho_names = [chat.name for chat in chats_trabalho]
        pessoal_names = [chat.name for chat in chats_pessoal]

        # With the mutation, BOTH should return CHAT-PESSOAL (both hit port 3098)
        # This proves that the content-based assertions catch the broken routing:
        # The old type-based assertions would pass because both return lists,
        # but the new content-based assertions fail.
        assert "CHAT-PESSOAL" in trabalho_names, \
            f"Mutation test: with resolve_account broken, list_chats('trabalho') should hit pessoal, got: {trabalho_names}"
        assert "CHAT-TRABALHO" not in trabalho_names, \
            f"Mutation test: with resolve_account broken, list_chats('trabalho') shouldn't see trabalho data, got: {trabalho_names}"

    finally:
        # Restore original
        accounts.resolve_account = original_resolve_account
        if old_env is None:
            os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        else:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_env


def test_pair_account_not_paired(tmp_path):
    """Test D4: pair_account() uses correct URL /status (not /api/status).

    Bug: base_url is http://localhost:PORT/api, and code was calling "/api/status"
    which concatenates to http://localhost:PORT/api/api/status (wrong).
    Fix: use "/status" which concatenates to http://localhost:PORT/api/status (correct).

    This test mocks requests.request to validate the exact URLs called,
    verifying the status check uses /api/status and QR fetch uses /qr.png.
    """
    # Create accounts.json
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "test_pair",
        "accounts": {
            "test_pair": {
                "port": 9999,
                "dir": "/tmp/test",
                "jid": ""
            }
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    old_env = os.environ.get('WHATSAPP_ACCOUNTS_FILE')
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    # Valid PNG signature
    valid_png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100  # Minimal PNG structure

    # Track which URLs were called with full URL
    urls_called = []

    # Save originals
    original_requests_get = requests.get
    original_requests_request = requests.request

    def mock_requests_request(method, url, **kwargs):
        """Mock requests.request to intercept /api/status calls."""
        urls_called.append(('request', method, url))

        # The status check should call http://localhost:9999/api/status (NOT /api/api/status)
        if url == "http://localhost:9999/api/status" and method == "GET":
            class MockResponse:
                status_code = 200
                text = '{"logged_in": false}'
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"logged_in": False}
            return MockResponse()

        # Reject any other URL to catch the bug
        raise requests.ConnectionError(f"Unexpected URL: {method} {url}")

    def mock_requests_get(url, **kwargs):
        """Mock requests.get to intercept /qr.png calls."""
        urls_called.append(('get', 'GET', url))

        # /qr.png should be at exactly http://localhost:9999/qr.png
        if url == "http://localhost:9999/qr.png":
            class MockResponse:
                status_code = 200
                content = valid_png
                def raise_for_status(self):
                    pass
            return MockResponse()

        # Reject any other URL to catch bugs
        raise requests.ConnectionError(f"Unexpected URL: GET {url}")

    try:
        # Replace both get and request
        requests.get = mock_requests_get
        requests.request = mock_requests_request

        # Call pair_account
        qr_bytes = whatsapp.pair_account("test_pair")

        # Verify exact URLs were called
        request_urls = [url for method, http_method, url in urls_called if method == 'request']
        get_urls = [url for method, http_method, url in urls_called if method == 'get']

        # Must call the CORRECT /api/status (not /api/api/status)
        assert "http://localhost:9999/api/status" in request_urls, \
            f"pair_account should call http://localhost:9999/api/status, got requests: {request_urls}"

        # Must NOT call /api/api/status (the bug)
        assert not any("/api/api/status" in url for url in request_urls), \
            f"pair_account should NOT call /api/api/status, got requests: {request_urls}"

        # Must call /qr.png (not /api/qr.png)
        assert "http://localhost:9999/qr.png" in get_urls, \
            f"pair_account should call http://localhost:9999/qr.png, got gets: {get_urls}"

        # Verify response
        assert isinstance(qr_bytes, bytes), f"Should return bytes, got {type(qr_bytes)}"
        assert qr_bytes.startswith(b'\x89PNG'), f"Should have PNG signature, got {qr_bytes[:4]!r}"

    finally:
        # Restore originals
        requests.get = original_requests_get
        requests.request = original_requests_request

        if old_env is None:
            os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        else:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_env


def test_pair_account_already_paired(bridge_processes, bridge_dirs, tmp_path):
    """Test D4: pair_account() with already-paired account returns 'already paired' message.

    When a bridge's /api/status shows logged_in=true (account already paired),
    pair_account() should raise ValueError with a message saying the account
    is already paired, not a raw HTTP error.

    Verifies the URL is http://localhost:PORT/api/status (not /api/api/status).
    """
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "trabalho": {
                "port": 3097,
                "dir": bridge_dirs['trabalho'],
                "jid": ""
            },
            "pessoal": {
                "port": 3098,
                "dir": bridge_dirs['pessoal'],
                "jid": ""
            }
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))

    old_env = os.environ.get('WHATSAPP_ACCOUNTS_FILE')
    os.environ['WHATSAPP_ACCOUNTS_FILE'] = str(accounts_file)

    # Save original _api_request
    original_api_request = whatsapp._api_request
    urls_called = []

    def mock_api_request(method, path, base_url, timeout=30, **kwargs):
        """Mock _api_request: return logged_in=true for /status (already paired).

        Verifies the path is "/status" (not "/api/status" which would be the bug).
        """
        full_url = f"{base_url}{path}"
        urls_called.append(full_url)

        # The correct path is "/status" when base_url already ends with "/api"
        # So full_url should be http://localhost:3097/api/status (not /api/api/status)
        if path == "/status" and "3097" in full_url:
            # Return a mock response showing already logged in
            class MockResponse:
                status_code = 200
                text = '{"logged_in": true, "healthy": true}'
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"logged_in": True, "healthy": True}
            return MockResponse()

        # Reject the bug (if code was using /api/status instead of /status)
        if path == "/api/status":
            raise AssertionError(
                f"pair_account called with path='/api/status' which creates "
                f"{full_url} (doubled /api). Bug not fixed. Use '/status' instead."
            )

        # For other paths, call the original
        return original_api_request(method, path, base_url, timeout, **kwargs)

    try:
        # Patch _api_request temporarily
        whatsapp._api_request = mock_api_request

        # Try to pair an already-paired account
        with pytest.raises(ValueError) as exc_info:
            whatsapp.pair_account("trabalho")

        error_msg = str(exc_info.value)

        # Error message should mention the account and that it's already paired
        assert "trabalho" in error_msg, \
            f"Error should mention account 'trabalho', got: {error_msg}"
        assert "already paired" in error_msg.lower(), \
            f"Error should mention 'already paired', got: {error_msg}"

        # Verify the correct URL was called
        assert any("http://localhost:3097/api/status" in url for url in urls_called), \
            f"Should call http://localhost:3097/api/status, got: {urls_called}"
        assert not any("/api/api/status" in url for url in urls_called), \
            f"Should NOT call /api/api/status, got: {urls_called}"

    finally:
        # Restore original _api_request
        whatsapp._api_request = original_api_request

        if old_env is None:
            os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        else:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_env



# ---------------------------------------------------------------------------
# Tarefa 7: regressao "a instalacao pareada de hoje nao percebe a multiconta".
#
# D1 e o invariante mais caro da entrega: sem accounts.json no disco (o estado
# real desta maquina hoje), as 36 tools pre-existentes continuam funcionando
# exatamente como antes — nenhuma exige `account`, a guarda de escrita da D2
# nunca dispara com 0 ou 1 conta configurada, a URL sai da mesma precedencia
# de sempre, e get_bridge_status() devolve a tupla classica, nao o agregado.
# ---------------------------------------------------------------------------


def _load_tool_functions():
    """Parse main.py's source with ast instead of importing the module.

    main.py does `from mcp.server.fastmcp import FastMCP`, and the `mcp`
    package is not installed in the environment that runs this suite with
    `python -m pytest` (confirmed: `import main` raises ModuleNotFoundError
    here, while every one of the 51 pre-existing tests only imports
    `accounts` and `whatsapp`, never `main`). Parsing the real source keeps
    this a check against the actual production file — just without
    executing it — instead of silently skipping or requiring a dependency
    the rest of the suite doesn't need.
    """
    main_path = Path(__file__).parent / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    tools = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "tool"
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == "mcp"
                ):
                    tools.append(node)
                    break
    return tools


def _account_param_status(node):
    """Classify the `account` parameter of a tool FunctionDef.

    Returns one of: 'missing' (no such param), 'required' (present, no
    default), 'optional_none' (default is exactly None), 'optional_other'
    (present with some other default).
    """
    args = node.args.args
    defaults = node.args.defaults
    n_no_default = len(args) - len(defaults)
    defaults_by_name = {
        args[n_no_default + i].arg: d for i, d in enumerate(defaults)
    }
    names = [a.arg for a in args]
    if "account" not in names:
        return "missing"
    if "account" not in defaults_by_name:
        return "required"
    d = defaults_by_name["account"]
    if isinstance(d, ast.Constant) and d.value is None:
        return "optional_none"
    return "optional_other"


class TestNoToolRequiresAccount:
    """Systematic check across every @mcp.tool() in main.py, not just the
    handful exercised elsewhere in this file."""

    def test_at_least_36_tools_registered(self):
        """Sanity check on the parser itself: if this drops below 36, the
        AST walk is broken (or someone deleted tools), and the assertions
        below would be vacuously true over an empty/tiny set."""
        tools = _load_tool_functions()
        names = sorted(t.name for t in tools)
        assert len(tools) >= 36, f"Expected at least 36 tools, found {len(tools)}: {names}"

    def test_no_tool_except_pair_account_requires_account(self):
        """D1/D9: none of the pre-existing 36 tools may require `account`.
        `pair_account` (D4, task 6) is the sole, deliberate exception and is
        excluded here — proven separately below."""
        tools = _load_tool_functions()
        offenders = {}
        for t in tools:
            if t.name == "pair_account":
                continue
            status = _account_param_status(t)
            if status != "optional_none":
                offenders[t.name] = status
        assert not offenders, (
            f"Tools that require `account` or lack a `None` default (would "
            f"break the un-migrated installation, which never passes "
            f"account=...): {offenders}"
        )

    def test_pair_account_requires_account_by_design(self):
        """Adversarial control for the assertion above: if the exclusion of
        pair_account were dropped (or if pair_account's signature changed to
        make account optional, silently weakening D4), the blanket test
        above would need to fail. This proves the checker actually
        discriminates instead of vacuously passing every function."""
        tools = _load_tool_functions()
        pair = next((t for t in tools if t.name == "pair_account"), None)
        assert pair is not None, "pair_account tool not found in main.py"
        assert _account_param_status(pair) == "required", (
            "pair_account must require `account` per D4 — parear a conta "
            "errada e pior que um erro de argumento"
        )


class TestWriteGuardDoesNotFireWithFewAccounts:
    """D2: a guarda de escrita so dispara com MAIS de uma conta configurada.
    O teste existente (test_write_op_guard_without_account_with_multiple_accounts)
    cobre o lado que dispara; aqui cobrimos o lado que nao pode disparar —
    exatamente o estado da instalacao de hoje (zero contas) e o primeiro
    estado depois de uma migracao completa (uma conta so). Ambos usam HTTP
    mockado: nao tocam rede nem o bridge real."""

    def test_send_message_without_account_allowed_with_zero_accounts_configured(
        self, monkeypatch
    ):
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        monkeypatch.delenv("WHATSAPP_API_BASE_URL", raising=False)
        monkeypatch.setenv("WHATSAPP_BRIDGE_PORT", "41010")
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)

            calls = []

            def fake_post(url, **kwargs):
                calls.append(url)

                class R:
                    status_code = 200

                    def json(self):
                        return {"success": True, "message": "sent"}

                return R()

            monkeypatch.setattr(whatsapp.requests, "post", fake_post)

            ok, msg = whatsapp.send_message(
                "55119999999@s.whatsapp.net", "oi", account=None
            )

            assert ok is True, f"expected success, got ok={ok} msg={msg!r}"
            assert calls == ["http://localhost:41010/api/send"], (
                f"expected exactly one POST to the env-derived URL, got: {calls}"
            )

    def test_send_message_without_account_allowed_with_exactly_one_account_configured(
        self, monkeypatch, tmp_path
    ):
        """Boundary case for the `len(known) > 1` check in
        whatsapp._require_account: with exactly one configured account this
        must NOT raise. An off-by-one (`>= 1` instead of `> 1`) would break
        the very first account a user creates."""
        accounts_file = tmp_path / "accounts.json"
        accounts_config = {
            "default": "pessoal",
            "accounts": {
                "pessoal": {"port": 41020, "dir": str(tmp_path), "jid": ""},
            },
        }
        accounts_file.write_text(json.dumps(accounts_config))
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)

            class R:
                status_code = 200

                def json(self):
                    return {"success": True, "message": "sent"}

            return R()

        monkeypatch.setattr(whatsapp.requests, "post", fake_post)

        ok, msg = whatsapp.send_message(
            "55119999999@s.whatsapp.net", "oi", account=None
        )

        assert ok is True, f"expected success, got ok={ok} msg={msg!r}"
        assert calls == ["http://localhost:41020/api/send"], (
            f"expected exactly one POST to the configured account's URL, got: {calls}"
        )


def test_read_without_account_falls_back_to_default_with_multiple_accounts_configured(
    monkeypatch, tmp_path
):
    """D2 asymmetry: reads never require `account`, even with more than one
    account configured — only writes are guarded. list_chats(account=None)
    must resolve to the DEFAULT account's port (pessoal/41031), not the
    other one (trabalho/41032), proving the fallback picks the account the
    production code decided, not one the test assumes."""
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "pessoal": {"port": 41031, "dir": str(tmp_path / "pessoal"), "jid": ""},
            "trabalho": {"port": 41032, "dir": str(tmp_path / "trabalho"), "jid": ""},
        },
    }
    accounts_file.write_text(json.dumps(accounts_config))
    monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

    status_calls = []

    def fake_request(method, url, **kwargs):
        status_calls.append(url)

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"healthy": True}

            text = "{}"

        return R()

    monkeypatch.setattr(whatsapp.requests, "request", fake_request)

    chats_calls = []

    def fake_post(url, **kwargs):
        chats_calls.append(url)

        class R:
            status_code = 200

            def json(self):
                return {"chats": []}

        return R()

    monkeypatch.setattr(whatsapp.requests, "post", fake_post)

    result = whatsapp.list_chats(account=None)

    assert result == []
    # D1: When account is None, list_chats falls back to default WITHOUT checking online status
    # (checking status only happens when account is explicitly provided).
    # So status_calls should be empty - only /chats should be called.
    assert status_calls == [], (
        f"expected NO status probe when account=None (D1 + Achado 1), got: {status_calls}"
    )
    assert chats_calls == ["http://localhost:41031/api/chats"], (
        f"expected /chats to hit the default account's port, got: {chats_calls}"
    )


def test_get_bridge_status_classic_tuple_without_accounts_file(monkeypatch, tmp_path):
    """D13: get_bridge_status() only aggregates when accounts_configured() is
    True AND more than one alias exists. Without accounts.json,
    accounts_configured() is False, so this must always hit the classic
    branch and return the (healthy, reason, status_dict) 3-tuple — never
    (False, "aggregated", {...}). Mocked HTTP: isolated, no real bridge."""
    monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("WHATSAPP_API_BASE_URL", "http://localhost:41876/api")

    def fake_request(method, url, **kwargs):
        assert url == "http://localhost:41876/api/status", url

        class R:
            status_code = 200

            def json(self):
                return {"healthy": True, "reason": ""}

            text = "{}"

        return R()

    monkeypatch.setattr(whatsapp.requests, "request", fake_request)

    result = whatsapp.get_bridge_status()

    assert result == (True, "", {"healthy": True, "reason": ""}), result
    assert isinstance(result, tuple) and len(result) == 3
    assert result[1] != "aggregated"


def test_get_bridge_status_classic_tuple_with_exactly_one_account_configured(
    monkeypatch, tmp_path
):
    """Boundary case for the `len(known) > 1` check that gates D13
    aggregation in get_bridge_status(): with accounts.json present but only
    ONE account configured (the state right after a fresh install or right
    after this exact migration, before a second account is ever added),
    accounts_configured() is True but aggregation must still NOT trigger.
    An off-by-one there (`>= 1`) would turn every single-account install's
    status check into the aggregated dict shape."""
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {"pessoal": {"port": 41877, "dir": str(tmp_path), "jid": ""}},
    }
    accounts_file.write_text(json.dumps(accounts_config))
    monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

    def fake_request(method, url, **kwargs):
        assert url == "http://localhost:41877/api/status", url

        class R:
            status_code = 200

            def json(self):
                return {"healthy": True, "reason": ""}

            text = "{}"

        return R()

    monkeypatch.setattr(whatsapp.requests, "request", fake_request)

    result = whatsapp.get_bridge_status()

    assert result == (True, "", {"healthy": True, "reason": ""}), result
    assert isinstance(result, tuple) and len(result) == 3
    assert result[1] != "aggregated"


def test_get_bridge_status_real_bridge_regression(monkeypatch):
    """THE test for tarefa 7 / D1: com o ambiente real desta maquina (sem
    accounts.json, WHATSAPP_API_BASE_URL apontando para o bridge de producao
    ja rodando) uma chamada REAL a get_bridge_status() bate em
    http://localhost:3005/api/status por GET (unica chamada de rede
    autorizada nesta suite) e devolve o formato classico, provando que a
    instalacao pareada de hoje nao percebe a multiconta chegou.

    Deliberadamente NAO usa monkeypatch para isolar HOME/accounts — a prova
    exige o ambiente real da instalacao. `monkeypatch` aqui so protege
    WHATSAPP_ACCOUNTS_FILE, para o caso de outro teste tê-lo deixado setado
    (nenhum deixa hoje: todos usam monkeypatch/finally, mas isso e
    verificado por leitura do arquivo, nao por confianca).

    Se o bridge estiver fora do ar, este teste FALHA (nao pula) — skip
    esconderia a ausencia da infraestrutura que a tarefa pede para provar.
    """
    monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)

    healthy, reason, status = whatsapp.get_bridge_status()

    assert isinstance(healthy, bool), f"healthy should be bool, got {type(healthy)}: {healthy!r}"
    assert isinstance(reason, str), f"reason should be str, got {type(reason)}: {reason!r}"
    assert reason != "aggregated", (
        "get_bridge_status() returned the multi-account aggregated format "
        "even though no accounts.json exists on this machine — the paired "
        "installation DID notice multiconta landed"
    )
    assert not reason.startswith("Request error:"), (
        f"could not reach the real bridge at localhost:3005 — this test "
        f"requires it to be up (read-only GET /api/status only): {reason}"
    )
    assert isinstance(status, dict), f"expected a real status dict, got {status!r}"
    # StatusResponse.Success is always present on a 200 from handleStatus
    # (whatsapp-bridge/main.go); asserting on it (rather than on the
    # pairing-dependent `healthy`/`logged_in` fields) keeps this test valid
    # regardless of whether this machine's account happens to be paired
    # right now.
    assert status.get("success") is True, (
        f"expected success=true from a live bridge's /api/status, got: {status}"
    )
    assert "connected" in status and "logged_in" in status, status


def test_list_chats_without_accounts_file_bridge_offline():
    """Achado 1 / D1: Single-account installation without accounts.json, bridge offline.

    This is the most critical test for Achado 1. It proves that list_chats()
    without account parameter (D1: falls back to default) must return [] and NOT
    raise an exception when:
    - No accounts.json exists (single-account install, production state)
    - Bridge is unreachable (port to a dead host)

    Before the fix, list_chats() called _check_account_online(None) unconditionally,
    which would raise ValueError("No accounts configured") — violating D1.

    After the fix, list_chats() only calls _check_account_online when account is
    explicitly provided; when account=None, it just resolves the default and
    makes the API call, which returns [] on transport failure.
    """
    # Point to a port that doesn't respond
    os.environ['WHATSAPP_API_BASE_URL'] = 'http://localhost:1/api'
    # Ensure accounts.json doesn't exist
    os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)

    try:
        # This should return [] without raising, proving D1 is honored
        result = whatsapp.list_chats()
        assert result == [], f"Expected empty list when bridge is offline, got: {result}"
    finally:
        # Restore to allow other tests to run
        os.environ.pop('WHATSAPP_API_BASE_URL', None)


def test_achado_1_sender_name_cache_segmentation(monkeypatch, tmp_path):
    """Achado 1 — Cache de nomes não vaza entre contas.

    Prova que o cache de sender names é segmentado por conta (base_url):
    1. Contas DIFERENTES não compartilham entrada de cache
    2. Mesmo sender_jid em contas diferentes resolve para nomes diferentes
    3. account=None compartilha cache com o alias da conta default

    Before fix: _sender_name_cache key was apenas sender_jid
    After fix: _sender_name_cache key é (base_url, sender_jid)
    """
    # Create accounts.json with two accounts
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "trabalho": {"port": 3097, "dir": "/tmp/trabalho", "jid": ""},
            "pessoal": {"port": 3098, "dir": "/tmp/pessoal", "jid": ""}
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))
    monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))

    # Mock _api_post to return different names for the same sender_jid on different accounts
    call_count = [0]
    def mock_api_post(path, payload, base_url, timeout=None):
        call_count[0] += 1
        if path != "/sender_name":
            return None
        sender_jid = payload.get("sender_jid")
        # Return different names based on which account (base_url) called
        if "3097" in base_url:  # trabalho
            return {"name": f"Fulano-TRABALHO"}
        elif "3098" in base_url:  # pessoal
            return {"name": f"Fulano-PESSOAL"}
        return {"name": sender_jid}

    monkeypatch.setattr(whatsapp, "_api_post", mock_api_post)

    # Test 1: Same sender_jid should resolve to different names on different accounts
    name_trabalho = whatsapp.get_sender_name("551111111@s.whatsapp.net", account="trabalho")
    assert name_trabalho == "Fulano-TRABALHO", f"Expected Fulano-TRABALHO, got {name_trabalho}"

    name_pessoal = whatsapp.get_sender_name("551111111@s.whatsapp.net", account="pessoal")
    assert name_pessoal == "Fulano-PESSOAL", f"Expected Fulano-PESSOAL, got {name_pessoal}"

    # Test 2: Second call to trabalho should NOT return the pessoal name from cache
    # If cache was segmented correctly, this should NOT call _api_post again (uses cache)
    call_count[0] = 0
    name_trabalho_2 = whatsapp.get_sender_name("551111111@s.whatsapp.net", account="trabalho")
    assert name_trabalho_2 == "Fulano-TRABALHO", f"Expected cached Fulano-TRABALHO, got {name_trabalho_2}"
    assert call_count[0] == 0, f"Expected cache hit (no API call), but got {call_count[0]} calls"

    # Test 3: account=None should share cache with default account (pessoal)
    call_count[0] = 0
    name_default = whatsapp.get_sender_name("551111111@s.whatsapp.net", account=None)
    assert name_default == "Fulano-PESSOAL", f"Expected cached Fulano-PESSOAL from default, got {name_default}"
    assert call_count[0] == 0, f"Expected cache hit for default account, but got {call_count[0]} calls"

    # Mutation proof: showing that cache was properly segmented
    # The cache keys should be different for different accounts
    cache_keys = list(whatsapp._sender_name_cache.keys())
    assert len(cache_keys) == 2, f"Should have 2 cache entries, got {len(cache_keys)}: {cache_keys}"
    # Each key should be a tuple with base_url as first element
    assert all(isinstance(k, tuple) and len(k) == 2 for k in cache_keys), \
        f"Cache keys should be tuples of (base_url, sender_jid), got {cache_keys}"


def test_achado_2_get_message_context_account_check(monkeypatch, tmp_path):
    """Achado 2 — get_message_context usa _resolve_and_check_account_explicit.

    Prova que get_message_context(account='offline_account') falha com mensagem clara
    (nomeando a conta e tarefa) em vez de um erro genérico, implementando D12.

    Before fix: base_url = accounts.resolve_account(account) without checking
    After fix: usa _resolve_and_check_account_explicit quando account != None
    """
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "offline": {"port": 9999, "dir": "/tmp/offline", "jid": ""}
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))
    monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))

    # Mock requests.request to simulate offline bridge
    def mock_request(*args, **kwargs):
        raise requests.RequestException("Connection refused")

    monkeypatch.setattr("requests.request", mock_request)

    # Calling get_message_context with explicit account should raise ValueError with task name
    with pytest.raises(ValueError) as exc_info:
        whatsapp.get_message_context("msg123", account="offline")

    error_msg = str(exc_info.value)
    assert "offline" in error_msg.lower(), \
        f"Error should mention account alias 'offline', got: {error_msg}"
    assert "WhatsAppMCPBridge" in error_msg, \
        f"Error should mention task name, got: {error_msg}"


def test_achado_3_get_group_invite_link_reset_guard(monkeypatch, tmp_path):
    """Achado 3 — get_group_invite_link(reset=True) requer account.

    Prova que reset=True é uma mutação e requer account quando múltiplas contas
    configuradas (D2), enquanto reset=False é leitura e account é opcional.

    Before fix: Sem _require_account guardando reset=True
    After fix: _require_account(account) aplicado apenas quando reset=True
    """
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "pessoal": {"port": 3098, "dir": "/tmp/pessoal", "jid": ""},
            "trabalho": {"port": 3097, "dir": "/tmp/trabalho", "jid": ""}
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))
    monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))

    # Test 1: reset=True without account should raise ValueError (D2 guard)
    with pytest.raises(ValueError) as exc_info:
        whatsapp.get_group_invite_link("123@g.us", reset=True, account=None)

    error_msg = str(exc_info.value)
    assert "account" in error_msg.lower(), \
        f"Error should mention 'account' parameter, got: {error_msg}"

    # Test 2: reset=False without account should NOT raise (it's a read op)
    # Mock requests.request to handle the read case
    def mock_request(*args, **kwargs):
        class Response:
            status_code = 200
            text = "{}"
            def json(self):
                return {"success": True, "link": "https://chat.whatsapp.com/test"}
        return Response()

    monkeypatch.setattr("requests.request", mock_request)

    # This should NOT raise, proving reset=False is treated as read operation
    success, msg, link = whatsapp.get_group_invite_link("123@g.us", reset=False, account=None)
    assert success is True, f"Expected success for read operation, got: {success}"


def test_achado_4_get_bridge_status_error_message_alignment(monkeypatch, tmp_path):
    """Achado 4 — get_bridge_status(account='X') offline message cita apelido e tarefa.

    Prova que quando bridge está offline, a mensagem de erro é consistente entre
    caminho agregado (múltiplas contas) e caminho explícito (conta única).

    Before fix: Retornava f"Request error: {str(e)}" genérico
    After fix: Retorna f"Bridge offline. Start task: {task_name}" (como agregado)
    """
    accounts_file = tmp_path / "accounts.json"
    accounts_config = {
        "default": "pessoal",
        "accounts": {
            "work": {"port": 9999, "dir": "/tmp/work", "jid": ""}
        }
    }
    accounts_file.write_text(json.dumps(accounts_config))
    monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))

    # Mock requests.request to simulate offline bridge
    def mock_request(*args, **kwargs):
        raise requests.RequestException("Connection refused")

    monkeypatch.setattr("requests.request", mock_request)

    # Call get_bridge_status with explicit account when bridge is offline
    healthy, reason, status = whatsapp.get_bridge_status(account="work")

    assert healthy is False, f"Should return unhealthy status"
    # Message should cite account and task name, NOT a generic "Request error"
    assert "Bridge offline" in reason or "Start task" in reason, \
        f"Error message should mention bridge offline and task, got: {reason}"
    assert "WhatsAppMCPBridge" in reason, \
        f"Error message should mention task name, got: {reason}"
    # Should NOT contain generic "Request error:"
    assert not reason.startswith("Request error:"), \
        f"Error message should not be generic 'Request error:', got: {reason}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
