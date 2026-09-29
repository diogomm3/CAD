"""Small async adapter around Scrapling's browser-backed fetcher."""

from dataclasses import dataclass

from ..config import HTTP_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ScraplingResponse:
    url: str
    status: int
    headers: dict
    body: bytes
    encoding: str

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding or "utf-8", errors="replace")


async def fetch(
    url: str,
    *,
    cookies: dict[str, str] | None = None,
    page_action=None,
    page_setup=None,
) -> ScraplingResponse:
    """Fetch a page or file with Scrapling and return normalized response data."""
    from scrapling.fetchers import StealthyFetcher

    page = await StealthyFetcher.async_fetch(
        url,
        solve_cloudflare=True,
        timeout=int(HTTP_TIMEOUT_SECONDS * 1000),
        network_idle=False,
        wait=1000,
        retries=1,
        google_search=False,
        cookies=cookies,
        page_action=page_action,
        page_setup=page_setup,
    )
    return ScraplingResponse(
        url=str(page.url),
        status=int(page.status),
        headers=dict(page.headers),
        body=page.body,
        encoding=str(getattr(page, "encoding", "utf-8") or "utf-8"),
    )
