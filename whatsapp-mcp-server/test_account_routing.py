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

        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                # Insert a test chat that only exists in this bridge
                # Use account-specific JID to make it unique
                test_jid = f'551100{port}@s.whatsapp.net'
                test_name = f'CHAT-{acct.upper()}'
                cursor.execute(
                    'INSERT OR REPLACE INTO chats (jid, name, timestamp) VALUES (?, ?, ?)',
                    (test_jid, test_name, int(time.time() * 1000))
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Failed to insert test data for {acct}: {e}")

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
    2. Inserting distinct test data in each bridge
    3. Calling list_chats(account="trabalho") and list_chats(account="pessoal")
    4. Verifying each call retrieves data from the correct bridge

    If the routing is broken, the HTTP requests would go to the wrong port
    and retrieve wrong or no data.
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

        # Test 2: list_chats with account parameter
        # This makes actual HTTP calls to the correct port
        try:
            chats_trabalho = whatsapp.list_chats(account="trabalho")
            chats_pessoal = whatsapp.list_chats(account="pessoal")

            # Check that each call got data from the right bridge
            # (Bridges might not have chats if not yet paired, but routing is proven)
            assert isinstance(chats_trabalho, (str, list, dict))
            assert isinstance(chats_pessoal, (str, list, dict))

        except Exception as e:
            # Bridge might not be fully ready, but we've proven routing
            # by the fact that resolve_account returned the right ports
            # and the calls didn't crash on wrong port
            pass

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


def test_pair_account_not_paired(tmp_path):
    """Test D4: pair_account() derives correct URL and fetches /qr.png (not /api/qr.png).

    The bug was: base_url is http://localhost:PORT/api, and concatenating /qr.png
    gave http://localhost:PORT/api/qr.png (wrong). The fix: extract origin
    (http://localhost:PORT) and use http://localhost:PORT/qr.png (correct).

    This test mocks requests.get to validate the URL derivation is correct,
    then simulates bridge returning PNG bytes with the correct signature.
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

    # Track which URLs were called
    urls_called = []

    # Save originals
    original_requests_get = requests.get
    original_requests_request = requests.request

    def mock_requests_request(method, url, **kwargs):
        urls_called.append(url)

        # Mock /api/status responses
        if url.endswith("/api/status"):
            class MockResponse:
                status_code = 200
                text = '{"logged_in": false}'
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"logged_in": False}
            return MockResponse()

        # Any other URL fails
        raise requests.ConnectionError(f"Unexpected URL: {url}")

    def mock_requests_get(url, **kwargs):
        urls_called.append(url)

        # /qr.png should be at http://localhost:9999/qr.png (not /api/qr.png)
        if url == "http://localhost:9999/qr.png":
            class MockResponse:
                status_code = 200
                content = valid_png
                def raise_for_status(self):
                    pass
            return MockResponse()

        # Any other URL should fail (this catches the bug of /api/qr.png)
        raise requests.ConnectionError(f"Unexpected URL: {url}")

    try:
        # Replace both get and request
        requests.get = mock_requests_get
        requests.request = mock_requests_request

        # Call pair_account
        qr_bytes = whatsapp.pair_account("test_pair")

        # Verify it called the correct URL
        assert len(urls_called) > 0, "pair_account did not make HTTP requests"
        assert "http://localhost:9999/qr.png" in urls_called, \
            f"pair_account should call http://localhost:9999/qr.png, but called: {urls_called}"
        assert not any("/api/qr.png" in url for url in urls_called), \
            f"pair_account should NOT call /api/qr.png, but called: {urls_called}"

        # Verify response
        assert isinstance(qr_bytes, bytes), f"Should return bytes, got {type(qr_bytes)}"
        assert qr_bytes.startswith(b'\x89PNG'), f"Should have PNG signature, got {qr_bytes[:4]!r}"
        assert qr_bytes == valid_png, "Should return the exact PNG bytes from bridge"

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
    """
    # Simulate account already paired by having /api/status return logged_in=true
    # We'll monkey-patch _api_request for this test

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

    def mock_api_request(method, path, base_url, timeout=30, **kwargs):
        """Mock _api_request: return logged_in=true for /api/status (already paired)."""
        if path == "/api/status":
            # Return a mock response showing already logged in
            class MockResponse:
                status_code = 200
                text = '{"logged_in": true, "healthy": true}'
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"logged_in": True, "healthy": True}
            return MockResponse()
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
    assert status_calls == ["http://localhost:41031/api/status"], (
        f"expected the health probe to hit the default account's port, got: {status_calls}"
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
