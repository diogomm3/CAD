from abc import ABC, abstractmethod
import asyncio, json, logging, re, time, uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from ..config import DEBUG_SCRAPERS, DEBUG_DIR, HTTP_TIMEOUT_SECONDS, PLAYWRIGHT_ENABLED, SCRAPLING_ENABLED, REQUEST_DELAY_MS, BROWSER_AUTH_ENABLED, MAX_FILE_SIZE_MB, source_config
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

    async def _scrapling_fetch(self, url: str, *, page_action=None, page_setup=None, cookies=None):
        """Fetch a public page or file with Scrapling's stealth browser fallback."""
        if not SCRAPLING_ENABLED:
            raise SourceError("UNSUPPORTED", "Scrapling fallback is disabled.", "scrapling")
        try:
            from .scrapling_client import fetch
            await self._respect_rate()
            return await fetch(url, page_action=page_action, page_setup=page_setup, cookies=cookies)
        except SourceError:
            raise
        except Exception as exc:
            if "Download is starting" not in str(exc):
                log.exception("Scrapling fetch failed for %s",urlparse(url)._replace(query="").geturl())
            raise SourceError("SCRAPLING_ERROR", f"Scrapling fallback failed: {type(exc).__name__}.", "scrapling") from exc

    async def _scrapling_html(self, url: str) -> str:
        response = await self._scrapling_fetch(url)
        if response.status != 200:
            raise SourceError(f"HTTP_{response.status}", f"Scrapling returned HTTP {response.status}.", "scrapling")
        html = response.text
        visible=BeautifulSoup(html,"lxml").get_text(" ",strip=True)
        if re.search(r"verify you are human|checking your browser|access denied", visible[:12000], re.I):
            raise SourceError("BLOCKED", "The page presented an access challenge.", "scrapling")
        self.last_method = "scrapling"
        return html

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
    async def search(self, query: str, limit: int = 12) -> list[ModelResult]: ...

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
        if html is None and mode in {"auto","scrapling"} and SCRAPLING_ENABLED:
            try:
                html=await self._scrapling_html(model_url)
            except SourceError as exc:
                http_error=exc
                if mode == "scrapling": raise
        if html is None:
            if mode == "browser" and PLAYWRIGHT_ENABLED: html=await self._browser_html(model_url)
            elif mode == "authenticated_browser" and PLAYWRIGHT_ENABLED: html=await self._browser_html(model_url,authenticated=True)
            elif mode == "auto" and PLAYWRIGHT_ENABLED:
                try:html=await self._browser_html(model_url)
                except SourceError:
                    if not BROWSER_AUTH_ENABLED:raise
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

    async def _scrapling_download_file(self, file: DownloadableFile, destination: Path) -> tuple[str | None, int]:
        """Use a solved Scrapling browser context to request and save a raw file."""
        request_errors=[]

        async def request_file(page):
            try:
                response=await page.context.request.get(file.url or "",timeout=int(HTTP_TIMEOUT_SECONDS*1000))
                self.validate_download_url(response.url)
                content_type=response.headers.get("content-type","").split(";",1)[0].lower()
                if response.status!=200:
                    raise ValueError(f"HTTP_{response.status}")
                if "text/html" in content_type:
                    raise ValueError("unexpected_content_type: Download URL returned HTML")
                payload=await response.body()
                if len(payload)>MAX_FILE_SIZE_MB*1024*1024:
                    raise ValueError("file_too_large")
                if not payload:
                    raise ValueError("empty_download")
                destination.write_bytes(payload)
                file.mime_type=content_type or None
                file.size_bytes=len(payload)
            except Exception as exc:
                request_errors.append(exc)

        page_url=f"https://www.{self.domains[0]}"
        try:
            await self._scrapling_fetch(page_url,page_action=request_file)
        except SourceError:
            if not destination.exists():
                raise
        if destination.exists():
            total=destination.stat().st_size
            if total>MAX_FILE_SIZE_MB*1024*1024:
                destination.unlink(missing_ok=True)
                raise ValueError("file_too_large")
            if total<=0:
                destination.unlink(missing_ok=True)
                raise ValueError("empty_download")
            return file.mime_type or "application/octet-stream",total
        if request_errors:
            error=request_errors[-1]
            if isinstance(error,ValueError):
                raise error
            raise SourceError("SCRAPLING_ERROR",f"Scrapling's browser request failed: {type(error).__name__}.","scrapling") from error
        raise ValueError("Scrapling browser request did not produce a file")

    async def download_file(self, file: DownloadableFile, destination: Path):
        self.validate_download_url(file.url or "")
        await self._respect_rate()
        try:
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
        except (httpx.HTTPError, ValueError) as original_error:
            if "file_too_large" in str(original_error):
                raise
            # Retry browser-backed fetches for providers that challenge plain HTTP.
            # Keep the provider-specific download-host allowlist on the final URL.
            destination.unlink(missing_ok=True)
            try:
                content_type,total=await self._scrapling_download_file(file,destination)
            except Exception:
                destination.unlink(missing_ok=True)
                if not SCRAPLING_ENABLED:
                    raise original_error
                raise
            file.mime_type=content_type
            file.size_bytes=total
        return destination

    async def download_browser_file(self, model_url: str, file: DownloadableFile, destination: Path):
        """Use the provider's normal browser download action when no direct URL is exposed."""
        if SCRAPLING_ENABLED:
            wanted={file.extension.lower(),Path(file.name).suffix.lower()}-{""}
            stem=Path(file.name).stem.lower()
            self._scrapling_download_actions=[]

            async def click_download(page):
                selector="a, button, [role=button]"
                for _ in range(3):
                    candidates=page.locator(selector)
                    actions=await candidates.evaluate_all(r"""nodes => nodes.slice(0, 200).map((el,index) => ({
                        index,
                        label: `${el.innerText || el.textContent || ''} ${el.getAttribute('href') || ''} ${el.getAttribute('aria-label') || ''}`
                            .replace(/\s+/g, ' ').trim().slice(0, 240)
                    }))""")
                    opened_list=False
                    self._scrapling_download_actions.extend(action["label"] for action in actions
                        if re.search(r"download|file|\.stl|\.3mf",action["label"],re.I))
                    list_action=next((item for item in actions if re.search(r"download\s+list|file\s+list",item["label"],re.I)),None)
                    if list_action:
                        await candidates.nth(list_action["index"]).click(timeout=3000)
                        await page.wait_for_timeout(800)
                        self._scrapling_download_page_text=(await page.locator("body").inner_text())[:3000]
                        opened_list=True
                    if opened_list:
                        continue
                    for action in actions:
                        haystack=action["label"].lower()
                        matches=bool(stem and stem in haystack) or any(ext in haystack for ext in wanted)
                        if not (matches or ("download" in haystack and "list" not in haystack)):
                            continue
                        try:
                            async with page.expect_download(timeout=4000) as pending:
                                await candidates.nth(action["index"]).click(timeout=2500)
                            download=await pending.value
                            self.validate_download_url(download.url)
                            await download.save_as(str(destination))
                            if destination.exists() and destination.stat().st_size:
                                return
                        except Exception:
                            continue
                    if not opened_list:
                        break

            try:
                await self._scrapling_fetch(model_url,page_action=click_download)
                if destination.exists() and destination.stat().st_size:
                    size=destination.stat().st_size
                    if size>MAX_FILE_SIZE_MB*1024*1024:
                        destination.unlink(missing_ok=True)
                        raise ValueError("file_too_large")
                    file.size_bytes=size
                    self.last_method="scrapling_download"
                    return destination
            except Exception as exc:
                destination.unlink(missing_ok=True)
                log.info("%s Scrapling browser download failed for %s: %s",self.label,file.name,type(exc).__name__)
        raise SourceError("DOWNLOAD_UNAVAILABLE",f"Scrapling did not expose a browser download for {file.name}.","scrapling")

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
