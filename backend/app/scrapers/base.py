from abc import ABC, abstractmethod
import asyncio, json, logging, re, time, uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from ..config import DEBUG_SCRAPERS, DEBUG_DIR, HTTP_TIMEOUT_SECONDS, PLAYWRIGHT_ENABLED, REQUEST_DELAY_MS, BROWSER_AUTH_ENABLED, MAX_FILE_SIZE_MB, source_config
from ..models import DownloadableFile, ModelResult
from ..utils.filenames import category_for
from .errors import SourceError

log = logging.getLogger(__name__)
FILE_EXTENSIONS = {".stl", ".3mf", ".step", ".stp", ".iges", ".igs", ".sldprt", ".sldasm", ".slddrw", ".f3d", ".scad", ".blend", ".fcstd", ".ipt", ".iam", ".prt", ".asm", ".catpart", ".catproduct", ".3dxml", ".obj", ".dxf", ".zip", ".rar", ".7z"}
TRACKING = {"fbclid", "gclid", "ref", "referrer", "utm_source", "utm_medium", "utm_campaign"}

def canonical_url(url: str, base: str | None = None) -> str:
    absolute = urljoin(base or "https://invalid.local", url.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname: return ""
    query = "&".join(piece for piece in parsed.query.split("&") if piece and piece.split("=", 1)[0].lower() not in TRACKING)
    return urlunparse(("https", parsed.netloc.lower(), parsed.path.rstrip("/") or "/", "", query, ""))

def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"): return tag["content"].strip()
    return None

def _author_from_soup(soup: BeautifulSoup) -> str | None:
    for sel in ('meta[name="author"]', '[rel="author"]', '[itemprop="author"]', '[data-testid*="author"]', '[class*="author"]'):
        node = soup.select_one(sel)
        if node:
            value = node.get("content") or node.get_text(" ", strip=True)
            if value and len(value) < 120: return value
    return None

class ModelSource(ABC):
    key: str
    label: str
    domains: tuple[str, ...]
    download_domains: tuple[str, ...] = ()
    search_url: str
    search_method = "http"
    last_status = "ready"
    last_message: str | None = None
    last_method: str | None = None

    @property
    def config(self):
        return source_config(self.key)

    def ensure_enabled(self):
        if not self.config["enabled"] or self.config["access_mode"] == "disabled":
            raise SourceError("SOURCE_DISABLED", f"{self.label} is disabled in source configuration.")

    async def _respect_rate(self):
        if not hasattr(self,"_request_lock"):self._request_lock=asyncio.Lock();self._last_request_at=0.0
        await self._request_lock.acquire()
        try:
            delay=REQUEST_DELAY_MS/1000-(time.monotonic()-self._last_request_at)
            if delay>0:await asyncio.sleep(delay)
            self._last_request_at=time.monotonic()
        finally:self._request_lock.release()

    async def _get_response(self, url: str, *, params=None, headers=None) -> httpx.Response:
        await self._respect_rate()
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/134.0 Safari/537.36", **(headers or {})}) as client:
                response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise SourceError("TIMEOUT", "The source request timed out.", "http") from exc
        except httpx.RequestError as exc:
            raise SourceError("NETWORK_ERROR", f"The source could not be reached: {type(exc).__name__}.", "http") from exc
        if response.status_code in {401, 403, 429}:
            message = {401:"Source requires authentication.",403:"Source returned HTTP 403.",429:"Source rate limit reached."}[response.status_code]
            raise SourceError(f"HTTP_{response.status_code}", message, "http")
        try: response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SourceError(f"HTTP_{response.status_code}", f"Source returned HTTP {response.status_code}.", "http") from exc
        return response

    async def _get(self, url: str) -> str:
        return (await self._get_response(url)).text

    async def _browser_html(self, url: str, authenticated: bool = False) -> str:
        if not PLAYWRIGHT_ENABLED:
            raise SourceError("UNSUPPORTED", "Browser fallback is disabled.", "http")
        try:
            from ..browser import BROWSER
            html, screenshot = await BROWSER.render(url,authenticated=authenticated)
            self._last_screenshot=screenshot
            if re.search(r"captcha|verify you are human|checking your browser|access denied", html, re.I):
                raise SourceError("BLOCKED", "The page presented an access challenge.", "playwright_authenticated" if authenticated else "playwright_public")
            self.last_method = "playwright_authenticated" if authenticated else "playwright_public"
            return html
        except SourceError: raise
        except Exception as exc:
            status=re.search(r"HTTP (401|403|429)",str(exc))
            if status:
                code={"401":"HTTP_401","403":"HTTP_403","429":"HTTP_429"}[status.group(1)]
                raise SourceError(code,f"Browser page returned HTTP {status.group(1)}.","playwright_authenticated" if authenticated else "playwright_public") from exc
            raise SourceError("BROWSER_UNAVAILABLE", f"Browser fallback unavailable: {type(exc).__name__}.", "playwright_authenticated" if authenticated else "playwright_public") from exc

    def save_debug(self, payload: str, suffix: str) -> None:
        if not DEBUG_SCRAPERS: return
        folder = DEBUG_DIR / self.key
        folder.mkdir(parents=True, exist_ok=True)
        token=uuid.uuid4().hex[:10]
        if suffix.endswith(".json"):
            try:
                value=json.loads(payload)
                def scrub(item):
                    if isinstance(item,dict):return {key:("[redacted]" if re.search(r"password|secret|token|cookie|authorization",key,re.I) else scrub(val)) for key,val in item.items()}
                    if isinstance(item,list):return [scrub(x) for x in item]
                    return item
                payload=json.dumps(scrub(value),ensure_ascii=False,indent=2)
            except ValueError: payload="[invalid JSON omitted]"
        else:
            soup=BeautifulSoup(payload,"lxml")
            for node in soup.select('script, input[type="password"], input[name*="token" i], input[name*="secret" i], input[name*="auth" i], input[name*="cookie" i]'):node.decompose()
            payload=str(soup)
        (folder / f"{token}{suffix}").write_text(payload, encoding="utf-8")
        screenshot=getattr(self,"_last_screenshot",None)
        if screenshot:(folder / f"{token}-screenshot.png").write_bytes(screenshot)

    def validate_url(self, url: str):
        p = urlparse(url)
        if p.scheme != "https" or not self._host_allowed(p.hostname,self.domains):
            raise ValueError("URL is not an HTTPS URL belonging to this source")

    def validate_download_url(self, url: str):
        p=urlparse(url)
        if p.scheme!="https" or not self._host_allowed(p.hostname,self.domains+self.download_domains):
            raise ValueError("URL is not an HTTPS download URL belonging to this source")

    @staticmethod
    def _host_allowed(host: str | None, domains: tuple[str,...]) -> bool:
        return bool(host and any(host==domain or host.endswith("."+domain) for domain in domains))

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[ModelResult]: ...

    async def get_model_details(self, model_url: str) -> ModelResult:
        self.ensure_enabled()
        self.validate_url(model_url)
        mode=self.config["access_mode"]
        if mode == "api": raise SourceError("UNSUPPORTED", f"{self.label} has no configured API detail adapter.", "api")
        html=None; http_error=None
        if mode in {"auto","http"}:
            try: html=await self._get(model_url);self.last_method="http"
            except SourceError as exc:
                http_error=exc
                if mode == "http": raise
        if html is None:
            if mode == "browser" and PLAYWRIGHT_ENABLED: html=await self._browser_html(model_url)
            elif mode == "authenticated_browser" and PLAYWRIGHT_ENABLED: html=await self._browser_html(model_url,authenticated=True)
            elif mode == "auto" and PLAYWRIGHT_ENABLED:
                try:html=await self._browser_html(model_url)
                except SourceError:
                    if not (BROWSER_AUTH_ENABLED and self.key!="thingiverse"):raise
                    html=await self._browser_html(model_url,authenticated=True)
            elif http_error: raise http_error
            else: raise SourceError("UNSUPPORTED", "No enabled access method is available.")
        detail=self.parse_detail(html, model_url)
        if not detail.available_files and PLAYWRIGHT_ENABLED and (mode=="auto" and self.last_method=="http"):
            try:
                rendered=await self._browser_html(model_url,authenticated=False)
                rendered_detail=self.parse_detail(rendered,model_url)
                if rendered_detail.available_files:return rendered_detail
            except SourceError as exc:log.info("%s file details browser fallback status=%s",self.label,exc.status)
        if not detail.available_files and PLAYWRIGHT_ENABLED and ((mode=="auto" and BROWSER_AUTH_ENABLED) or mode=="authenticated_browser"):
            try:
                rendered=await self._browser_html(model_url,authenticated=True)
                rendered_detail=self.parse_detail(rendered,model_url)
                if rendered_detail.available_files:return rendered_detail
            except SourceError as exc:log.info("%s authenticated file details fallback status=%s",self.label,exc.status)
        return detail

    async def get_downloads(self, model: ModelResult) -> list[DownloadableFile]:
        return model.available_files

    async def download_file(self, file: DownloadableFile, destination: Path):
        self.validate_download_url(file.url or "")
        await self._respect_rate()
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False,
            headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/134.0 Safari/537.36"}) as client:
            async with client.stream("GET", file.url) as response:
                response.raise_for_status()
                content_type=response.headers.get("content-type", "").split(";",1)[0].lower()
                if "text/html" in content_type: raise ValueError("unexpected_content_type: Download URL returned HTML")
                file.mime_type=content_type or None
                length=response.headers.get("content-length")
                if length and int(length)>MAX_FILE_SIZE_MB*1024*1024: raise ValueError("file_too_large")
                total=0
                with destination.open("wb") as out:
                    async for chunk in response.aiter_bytes():
                        total+=len(chunk)
                        if total>MAX_FILE_SIZE_MB*1024*1024:
                            destination.unlink(missing_ok=True);raise ValueError("file_too_large")
                        out.write(chunk)
                if total==0: raise ValueError("empty_download")
                file.size_bytes=total
        return destination

    def parse_detail(self, html: str, url: str) -> ModelResult:
        soup = BeautifulSoup(html, "lxml")
        canonical = soup.find("link", rel="canonical")
        page_url = canonical_url(canonical.get("href"), url) if canonical and canonical.get("href") else canonical_url(url)
        title = _meta(soup, "og:title", "twitter:title") or (soup.title.get_text(" ", strip=True) if soup.title else "")
        description = _meta(soup, "og:description", "description")
        image_url = _meta(soup, "og:image", "twitter:image")
        files, seen = [], set()
        for anchor in soup.select("a[href]"):
            href = canonical_url(anchor.get("href", ""), page_url)
            parsed = urlparse(href)
            raw_name = anchor.get("download") or anchor.get("data-file-name") or Path(parsed.path).name
            if Path(raw_name).suffix.lower() not in FILE_EXTENSIONS:
                from urllib.parse import parse_qs
                query=parse_qs(parsed.query)
                raw_name=query.get("filename",query.get("name",[raw_name]))[0]
            if Path(raw_name).suffix.lower() not in FILE_EXTENSIONS:
                for value in (anchor.get_text(" ",strip=True),anchor.get("title","")):
                    match=re.search(r"([^\s/\\]+\.(?:stl|3mf|step|stp|iges|igs|sldprt|sldasm|slddrw|f3d|scad|blend|fcstd|ipt|iam|prt|asm|catpart|catproduct|3dxml|obj|dxf|zip|rar|7z))",value,re.I)
                    if match:raw_name=match.group(1);break
            name = Path(raw_name).name
            ext = Path(name).suffix.lower()
            if ext not in FILE_EXTENSIONS or href in seen: continue
            seen.add(href)
            trusted = parsed.scheme == "https" and self._host_allowed(parsed.hostname,self.domains+self.download_domains)
            files.append(DownloadableFile(name=name, extension=ext, category=category_for(name), url=href,
                downloadable=trusted, reason=None if trusted else "Download host requires adapter validation",original_name=name,source_url=href))
        missing = {"title":not bool(title),"author":not bool(_author_from_soup(soup)),"thumbnail":not bool(image_url)}
        log.info("%sParser: detail page loaded; files discovered=%d; missing=%s", self.label, len(files), [k for k,v in missing.items() if v])
        counts={category:sum(f.category==category for f in files) for category in sorted({f.category for f in files})}
        log.info("%sParser: file category counts=%s",self.label,counts)
        return ModelResult(id=Path(urlparse(page_url).path).name, source=self.key, title=title or page_url,
            author=_author_from_soup(soup), model_url=page_url, thumbnail_url=canonical_url(image_url, page_url) if image_url else None,
            image_urls=[canonical_url(image_url, page_url)] if image_url else [], available_files=files,
            description=description, raw_metadata={"original_url":url,"canonical_url":page_url})
