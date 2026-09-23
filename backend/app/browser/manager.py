import asyncio
import logging
from playwright.async_api import async_playwright, BrowserContext, Page
from ..config import BROWSER_HEADLESS, BROWSER_PROFILE_DIR, HTTP_TIMEOUT_SECONDS

log = logging.getLogger(__name__)

class PersistentBrowser:
    """One lazily started Playwright context with a persistent local user profile."""
    def __init__(self):
        self._playwright = None
        self._context: BrowserContext | None = None
        self._public_browser = None
        self._public_context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def context(self) -> BrowserContext:
        async with self._lock:
            if self._context is None:
                BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                self._playwright = await async_playwright().start()
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_PROFILE_DIR), headless=BROWSER_HEADLESS,
                    viewport={"width":1365,"height":900}, locale="en-US",
                    timeout=int(HTTP_TIMEOUT_SECONDS * 1000))
                log.info("Started shared Playwright Chromium context (headless=%s)", BROWSER_HEADLESS)
            return self._context

    async def public_context(self) -> BrowserContext:
        async with self._lock:
            if self._public_context is None:
                if self._playwright is None:self._playwright=await async_playwright().start()
                self._public_browser=await self._playwright.chromium.launch(headless=True)
                self._public_context=await self._public_browser.new_context(viewport={"width":1365,"height":900},locale="en-US")
            return self._public_context

    async def new_page(self, authenticated: bool = False) -> Page:
        context=await self.context() if authenticated else await self.public_context()
        return await context.new_page()

    async def render(self, url: str, authenticated: bool = False) -> tuple[str, bytes | None]:
        page = await self.new_page(authenticated=authenticated)
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=int(HTTP_TIMEOUT_SECONDS * 1000))
            if response and response.status in {401,403,429}:
                raise RuntimeError(f"Browser {'authenticated' if authenticated else 'public'} page returned HTTP {response.status}")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1000)
            html = await page.content()
            from ..config import DEBUG_SCRAPERS
            screenshot = await page.screenshot(full_page=True) if DEBUG_SCRAPERS else None
            return html, screenshot
        finally:
            await page.close()

    async def close(self):
        async with self._lock:
            if self._context: await self._context.close()
            if self._public_context:await self._public_context.close()
            if self._public_browser:await self._public_browser.close()
            if self._playwright: await self._playwright.stop()
            self._context = self._public_context = self._public_browser = self._playwright = None

BROWSER = PersistentBrowser()
