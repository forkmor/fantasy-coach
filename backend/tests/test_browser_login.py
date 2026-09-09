import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error as BrowserError

from app.browser_login import BrowserLogin, TEAM_URL
from app.espn import EspnError
from app.security import SecretError, Vault
from app.storage import ConflictError, Store


COOKIES = [
    {"name": "espn_s2", "value": "synthetic-session", "domain": ".espn.com"},
    {"name": "SWID", "value": "{00000000-0000-0000-0000-000000000004}", "domain": ".espn.com"},
    {"name": "unrelated", "value": "must-not-save", "domain": ".espn.com"},
    {"name": "espn_s2", "value": "wrong-domain", "domain": "evil-espn.com"},
]


def fake_browser():
    page = SimpleNamespace(goto=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page),
                              cookies=AsyncMock(return_value=COOKIES),
                              storage_state=AsyncMock(return_value={"cookies": COOKIES, "origins": []}),
                              pages=[page])
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context),
                              close=AsyncMock(), is_connected=lambda: True)
    driver = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)),
                             stop=AsyncMock())
    factory = lambda: SimpleNamespace(start=AsyncMock(return_value=driver))
    return factory, driver, browser, context, page


def create_login(tmp_path, monkeypatch):
    store = Store(tmp_path / "db.sqlite")
    vault = Vault(store)
    monkeypatch.setattr(vault, "_crypt", lambda value: b"encrypted-placeholder")
    factory, driver, browser, context, page = fake_browser()
    espn = SimpleNamespace(test_connection=AsyncMock(return_value="Verified"))
    manager = BrowserLogin(store, vault, factory, lambda *args: espn)
    return manager, store, driver, browser, context, page, espn


def test_browser_sign_in_verifies_and_saves_espn_only_session(tmp_path, monkeypatch):
    manager, store, driver, browser, context, page, espn = create_login(tmp_path, monkeypatch)

    async def exercise():
        assert (await manager.start())["status"] == "waiting"
        page.goto.assert_awaited_once_with(TEAM_URL, wait_until="domcontentloaded", timeout=45000)
        assert not any(store.credential_status().values())
        result = await manager.finish()
        assert result["status"] == "connected" and not result["active"]
        espn.test_connection.assert_awaited_once()
        browser.close.assert_awaited_once()
        driver.stop.assert_awaited_once()
        assert store.credential_status() == {"grok": False, "gemini": False, "espn_s2": True, "swid": True}
        assert store.secret("espn_browser_state") is not None
        assert "synthetic-session" not in json.dumps(result)
        assert b"synthetic-session" not in store.path.read_bytes()
        assert b"must-not-save" not in store.path.read_bytes()

    asyncio.run(exercise())


def test_missing_cookies_and_wrong_account_preserve_existing_pair(tmp_path, monkeypatch):
    manager, store, _, _, context, _, espn = create_login(tmp_path, monkeypatch)
    store.put_secrets({"espn_s2": b"original-session", "swid": b"original-swid"})

    async def exercise():
        await manager.start()
        context.cookies.return_value = []
        assert (await manager.finish())["status"] == "waiting"
        espn.test_connection.assert_not_awaited()
        context.cookies.return_value = COOKIES
        espn.test_connection.side_effect = EspnError("ESPN team ownership could not be verified")
        result = await manager.finish()
        assert result["status"] == "waiting"
        assert "ownership" in result["detail"]
        assert store.secret("espn_s2") == b"original-session"
        assert store.secret("swid") == b"original-swid"
        await manager.cancel()

    asyncio.run(exercise())


def test_duplicate_start_cancel_and_expiry(tmp_path, monkeypatch):
    manager, store, _, browser, _, _, espn = create_login(tmp_path, monkeypatch)

    async def exercise():
        await manager.start()
        with pytest.raises(ConflictError, match="already"):
            await manager.start()
        assert (await manager.cancel())["status"] == "cancelled"
        await manager.start()
        manager.deadline = time.monotonic() - 1
        assert (await manager.finish())["status"] == "expired"
        assert browser.close.await_count == 2
        espn.test_connection.assert_not_awaited()
        assert not any(store.credential_status().values())

    asyncio.run(exercise())


def test_browser_start_failure_is_safe_and_retryable(tmp_path, monkeypatch):
    manager, store, driver, _, _, _, _ = create_login(tmp_path, monkeypatch)
    driver.chromium.launch.side_effect = BrowserError("private-browser-diagnostic")

    async def exercise():
        result = await manager.start()
        assert result["status"] == "failed"
        assert "private-browser-diagnostic" not in result["detail"]
        assert not result["active"]
        driver.stop.assert_awaited_once()
        assert not any(store.credential_status().values())

    asyncio.run(exercise())


def test_active_manager_blocks_browser_sign_in(tmp_path, monkeypatch):
    manager, store, driver, _, _, _, _ = create_login(tmp_path, monkeypatch)
    store.create_run({"provider": "grok", "model": "test", "mode": "dry_run"}, "test", 5)

    async def exercise():
        with pytest.raises(ConflictError, match="active manager"):
            await manager.start()
        driver.chromium.launch.assert_not_awaited()

    asyncio.run(exercise())


def test_encrypting_pair_is_atomic_on_encryption_failure(tmp_path, monkeypatch):
    store = Store(tmp_path / "db.sqlite")
    store.put_secrets({"espn_s2": b"original-session", "swid": b"original-swid"})
    vault = Vault(store)
    calls = 0

    def encrypt(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SecretError("Encryption unavailable")
        return b"new-encrypted-session"

    monkeypatch.setattr(vault, "_crypt", encrypt)
    with pytest.raises(SecretError):
        vault.put_many({"espn_s2": "new-session", "swid": "new-swid"})
    assert store.secret("espn_s2") == b"original-session"
    assert store.secret("swid") == b"original-swid"


def test_closed_window_is_detected_without_import(tmp_path, monkeypatch):
    manager, store, _, browser, context, _, _ = create_login(tmp_path, monkeypatch)

    async def exercise():
        await manager.start()
        context.pages = []
        await asyncio.wait_for(manager.watch_task, timeout=5)
        assert manager.state()["status"] == "cancelled"
        browser.close.assert_awaited_once()
        assert not any(store.credential_status().values())

    asyncio.run(exercise())
