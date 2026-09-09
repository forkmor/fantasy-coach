import asyncio
import json
import logging
import time
from urllib.parse import urlparse
from contextlib import suppress

try:
    from playwright.async_api import Error as BrowserError, async_playwright
except ModuleNotFoundError:
    class BrowserError(Exception):
        pass
    async_playwright = None

from .espn import EspnClient, EspnError
from .security import SecretError, Vault
from .storage import ConflictError, Store


TEAM_URL = "https://fantasy.espn.com/football/team?leagueId=656212638&teamId=4&seasonId=2026"
READ_URL = "https://lm-api-reads.fantasy.espn.com/"


class BrowserLogin:
    def __init__(self, store: Store, vault: Vault, browser_factory=async_playwright, espn_factory=EspnClient):
        self.store, self.vault = store, vault
        self.browser_factory, self.espn_factory = browser_factory, espn_factory
        self.playwright = self.browser = self.context = None
        self.watch_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.deadline = 0.0
        self.status = "idle"
        self.detail = "Sign in in a separate browser; passwords and verification codes stay with ESPN."

    @property
    def active(self):
        return self.status in {"opening", "waiting", "verifying"}

    def state(self):
        return {"status": self.status, "detail": self.detail, "active": self.active}

    def _set(self, status: str, detail: str):
        self.status, self.detail = status, detail

    async def start(self):
        async with self.lock:
            if self.browser_factory is None:
                raise ConflictError("Browser-assisted ESPN sign-in is not installed on this server")
            if self.active:
                raise ConflictError("An ESPN sign-in window is already open")
            if self.store.active_run():
                raise ConflictError("Stop the active manager before signing in to ESPN")
            self._set("opening", "Opening a separate Chrome window for ESPN sign-in...")
            try:
                self.playwright = await self.browser_factory().start()
                self.browser = await self.playwright.chromium.launch(channel="chrome", headless=False)
                self.context = await self.browser.new_context()
                page = await self.context.new_page()
                await page.goto(TEAM_URL, wait_until="domcontentloaded", timeout=45000)
                self.deadline = time.monotonic() + 600
                self._set("waiting", "Sign in to ESPN in the opened window. Then return here and select Save ESPN session.")
                self.watch_task = asyncio.create_task(self._watch())
            except BrowserError:
                await self._close()
                self._set("failed", "Could not open ESPN in Chrome. Ensure Chrome is installed, then retry.")
                self.store.event("espn_browser_login_failed", {"stage": "open"})
            return self.state()

    async def finish(self):
        async with self.lock:
            if self.status != "waiting" or self.context is None:
                raise ConflictError("Open the ESPN sign-in window first")
            if time.monotonic() >= self.deadline:
                await self._close()
                self._set("expired", "The sign-in window expired. Start again.")
                return self.state()
            if self.store.active_run():
                raise ConflictError("Stop the active manager before saving ESPN credentials")
            self._set("verifying", "Verifying ownership of your ESPN team before saving the session...")
            try:
                cookies = await self.context.cookies()
                values = {}
                for name in ("espn_s2", "swid"):
                    matches = {cookie["value"] for cookie in cookies
                               if cookie["name"].lower() == name
                               and cookie["domain"].lstrip(".") in {
                                   "espn.com", "fantasy.espn.com", "lm-api-reads.fantasy.espn.com",
                               }
                               and cookie["value"]}
                    if len(matches) != 1:
                        self._set("waiting", "A single complete ESPN session was not found. Finish signing in, then retry.")
                        return self.state()
                    values[name] = matches.pop()
                await self.espn_factory(values["espn_s2"], values["swid"]).test_connection()
                espn_cookies = [
                    cookie for cookie in cookies
                    if (
                        cookie.get("domain", "").lstrip(".") == "espn.com"
                        or cookie.get("domain", "").lstrip(".").endswith(".espn.com")
                    )
                    and cookie.get("name")
                    and cookie.get("value")
                ]
                raw_state = await self.context.storage_state()
                origins = [
                    origin for origin in raw_state.get("origins", [])
                    if isinstance(origin, dict)
                    and (
                        (host := urlparse(origin.get("origin", "")).hostname) == "espn.com"
                        or (host is not None and host.endswith(".espn.com"))
                    )
                ]
                values["espn_browser_state"] = json.dumps(
                    {"cookies": espn_cookies, "origins": origins},
                    separators=(",", ":"),
                )
                self.vault.put_many(values)
                self.store.event("espn_browser_login_saved", {
                    "ownership_verified": True,
                    "browser_session_saved": True,
                    "espn_cookie_count": len(espn_cookies),
                })
                await self._close()
                self._set("connected", "ESPN team ownership verified. Session saved with Windows encryption.")
            except EspnError as exc:
                self._set("waiting", f"Session not saved: {exc}. Check the ESPN account and retry.")
            except SecretError as exc:
                self._set("waiting", f"Session not saved: {exc}")
            except BrowserError:
                await self._close()
                self._set("failed", "The sign-in browser was closed or disconnected. Start again.")
                self.store.event("espn_browser_login_failed", {"stage": "capture"})
            return self.state()

    async def cancel(self):
        async with self.lock:
            await self._close()
            self._set("cancelled", "Sign-in cancelled. Existing saved credentials were not changed.")
            return self.state()

    async def _watch(self):
        try:
            while True:
                await asyncio.sleep(2)
                async with self.lock:
                    if not self.active:
                        return
                    if time.monotonic() >= self.deadline:
                        await self._close()
                        self._set("expired", "The sign-in window expired after 10 minutes. Start again.")
                        return
                    if not self.browser or not self.browser.is_connected() or not self.context.pages:
                        await self._close()
                        self._set("cancelled", "The sign-in browser was closed. Existing credentials were not changed.")
                        return
        except BrowserError:
            async with self.lock:
                await self._close()
                self._set("failed", "The ESPN sign-in browser disconnected. Start again.")

    async def _close(self):
        if self.watch_task and self.watch_task is not asyncio.current_task():
            self.watch_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.watch_task
        self.watch_task = None
        try:
            if self.browser:
                await self.browser.close()
        except BrowserError:
            logging.getLogger("harness").warning("ESPN sign-in browser cleanup reported a browser error")
        finally:
            self.context = self.browser = None
            if self.playwright:
                try:
                    await self.playwright.stop()
                except BrowserError:
                    logging.getLogger("harness").warning("ESPN sign-in driver cleanup reported a browser error")
                finally:
                    self.playwright = None

    async def shutdown(self):
        async with self.lock:
            await self._close()
