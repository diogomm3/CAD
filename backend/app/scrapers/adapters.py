import logging, re
import asyncio
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from bs4 import BeautifulSoup
import httpx
from .base import ModelSource, canonical_url, _meta, _author_from_soup
from .errors import SourceError
from ..models import DownloadableFile, ModelResult
from ..utils.filenames import category_for
from ..config import BROWSER_AUTH_ENABLED, PLAYWRIGHT_ENABLED, SCRAPLING_ENABLED, source_config
from ..services.bambu_auth import BAMBU_API, load_bambu_token

log = logging.getLogger(__name__)

def _number(value: str | None) -> int | None:
    if not value: return None
    value=value.strip().lower().replace(",", "")
    match=re.search(r"(\d+(?:\.\d+)?)\s*([km]?)", value)
    if not match:return None
    return int(float(match.group(1)) * {"k":1000,"m":1000000,"":1}[match.group(2)])

def _popularity_key(model: ModelResult) -> tuple[float, float, float]:
    return (float(model.downloads or 0), float(model.likes or 0), float(model.rating or 0))

class HTMLSearchSource(ModelSource):
    def accept_url(self, url: str) -> bool: return True

    def _candidate_models(self, html: str, limit: int) -> list[ModelResult]:
        soup=BeautifulSoup(html,"lxml"); seen=set(); result=[]
        candidates=[];rejected={"missing_title":0,"invalid_url":0}
        for anchor in soup.select("a[href]"):
            url=canonical_url(anchor.get("href",""), self.search_url)
            host=urlparse(url).hostname or ""
            if not url:
                rejected["invalid_url"]+=1;continue
            if not any(host==domain or host.endswith("."+domain) for domain in self.domains) or not self.accept_url(url): continue
            if url in seen: continue
            seen.add(url); candidates.append(anchor)
            card=anchor.find_parent(["article","li"]) or anchor.find_parent(lambda tag: tag.has_attr("data-testid") and "card" in tag.get("data-testid","")) or anchor.parent
            if card:
                title_node=card.select_one("h1,h2,h3,h4,[itemprop='name'],[data-testid*='title'],[class*='title']")
                img=card.select_one("img")
                title=(title_node.get_text(" ",strip=True) if title_node else anchor.get("title") or anchor.get("aria-label") or (img.get("alt") if img else None) or anchor.get_text(" ",strip=True))
                if len(title)>250: title=title[:250]
                if not title or len(title)<3 or title.strip().lower() in {"view","open","download","details","more"}:
                    rejected["missing_title"]+=1;continue
                author_node=card.select_one("[itemprop='author'],[class*='author'],[data-testid*='author']")
                author=author_node.get("content") or author_node.get_text(" ",strip=True) if author_node else None
                image=(img.get("src") or img.get("data-src") or img.get("data-lazy-src") or "") if img else ""
                if not image and img and img.get("srcset"): image=img["srcset"].split(",")[-1].strip().split(" ")[0]
                text=card.get_text(" ",strip=True)
                likes=_number((card.select_one("[data-likes], [class*='like']") or {}).get("data-likes"))
                if likes is None:
                    match=re.search(r"([\d,.]+\s*[km]?)\s*(?:likes|favorites|makes)",text,re.I); likes=_number(match.group(1)) if match else None
                downloads_match=re.search(r"([\d,.]+\s*[km]?)\s*downloads?",text,re.I)
                downloads=_number(downloads_match.group(1)) if downloads_match else None
                rating_match=re.search(r"(?:rating|★)\s*([0-5](?:\.\d+)?)",text,re.I)
                rating=float(rating_match.group(1)) if rating_match else None
                model_id=Path(urlparse(url).path).name
                result.append(ModelResult(id=model_id,source=self.key,title=title,author=author or None,
                    model_url=url,thumbnail_url=canonical_url(image,url) if image else None,image_urls=[canonical_url(image,url)] if image else [],
                    likes=likes,downloads=downloads,rating=rating,popularity_value=downloads if downloads is not None else likes,
                    popularity_label="downloads" if downloads is not None else "likes" if likes is not None else None,
                    raw_metadata={"ranking_position":len(result)+1,"original_url":anchor.get("href")}))
            if len(result)>=limit:break
        self.last_parse_diagnostics={"results_discovered":len(candidates),"accepted":len(result),"rejected":rejected,"missing_fields":self._missing(result)}
        log.info("%sParser: search page loaded; results discovered=%d; accepted=%d; rejected=%s; missing fields=%s",
            self.label,len(candidates),len(result),rejected,self._missing(result))
        return result[:limit]

    @staticmethod
    def _missing(models):
        return {key:sum(not getattr(model,key) for model in models) for key in ("model_url","title","author","thumbnail_url")}

    async def search(self, query: str, limit: int = 12) -> list[ModelResult]:
        self.ensure_enabled()
        mode=source_config(self.key)["access_mode"]
        if mode == "api": raise SourceError("UNSUPPORTED", f"{self.label} does not provide a configured API adapter.", "api")
        url=self.search_url.format(query=quote_plus(query)); errors=[]; attempts={}
        self.last_attempts=attempts
        methods=("http","scrapling","playwright_public","playwright_authenticated")
        allowed={"auto":methods,"http":("http",),"scrapling":("scrapling",),"browser":("playwright_public",),"authenticated_browser":("playwright_authenticated",)}.get(mode,())
        for method in methods:
            if method not in allowed:
                attempts[method]="not configured";continue
            if method=="scrapling" and not SCRAPLING_ENABLED:
                attempts[method]="Scrapling is disabled"
                continue
            if method.startswith("playwright_") and not PLAYWRIGHT_ENABLED:
                attempts[method]="Playwright is disabled"
                if mode!="auto":errors.append(SourceError("UNSUPPORTED","Playwright is disabled.",method))
                continue
            if method=="playwright_authenticated" and not (BROWSER_AUTH_ENABLED or mode=="authenticated_browser"):
                attempts[method]="not configured (set BROWSER_AUTH_ENABLED=true after manual login)";continue
            try:
                if method=="http": html=await self._get(url);self.last_method="http"
                elif method=="scrapling": html=await self._scrapling_html(url)
                else: html=await self._browser_html(url,authenticated=method=="playwright_authenticated")
                results=self._candidate_models(html,limit)
                if results:
                    attempts[method]=f"success ({len(results)} results)";self.last_status="success";self.last_message=None
                    self.last_method=method;return results
                attempts[method]="loaded; no model links parsed"
                if re.search(r"no (?:models|results|things) found|no results for",BeautifulSoup(html,"lxml").get_text(" ",strip=True),re.I):
                    self.last_status="success_empty";self.last_message=None;self.last_method=method;return []
                if html:self.save_debug(html,f"-{method}-search.html")
                errors.append(SourceError("PARSE_EMPTY","Page loaded, but no model cards matched the parser.",method))
            except SourceError as exc:
                attempts[method]=f"{exc.code}: {exc.message}";errors.append(exc)
        for error in reversed(errors):
            if error.status in {"blocked","authentication_required","rate_limited"}: raise error
        if errors: raise errors[-1]
        raise SourceError("UNSUPPORTED", "No access method is enabled for this source.")

class PrintablesSource(HTMLSearchSource):
    key="printables";label="Printables";domains=("printables.com",);download_domains=("media.printables.com","files.printables.com");search_method="graphql"
    search_url="https://www.printables.com/search/models?q={query}&o=popular"
    def accept_url(self,url):return bool(re.search(r"/model/\d+",urlparse(url).path))

    async def get_model_details(self, model_url: str) -> ModelResult:
        self.ensure_enabled(); self.validate_url(model_url)
        match=re.search(r"/model/(\d+)",urlparse(model_url).path)
        if not match:return await super().get_model_details(model_url)
        gql="""query PrintDetails($id: ID!) {
          print(id: $id) { id name slug stls { id name fileSize filePreviewPath } image { filePath } user { publicUsername } }
        }"""
        try:
            payload=await self._graphql(gql,{"id":match.group(1)})
            item=(payload.get("data") or {}).get("print")
            if not item:return await super().get_model_details(model_url)
            files=[]
            for item_file in item.get("stls") or []:
                name=item_file.get("name") or f"{item_file.get('id','model')}.stl"
                files.append(DownloadableFile(name=name,extension=Path(name).suffix.lower() or ".stl",category=category_for(name),size_bytes=item_file.get("fileSize"),downloadable=False,reason="Resolving Printables download URL.",provider_file_id=str(item_file.get("id") or "") or None))
            image_path=(item.get("image") or {}).get("filePath")
            image=f"https://media.printables.com/{image_path.lstrip('/')}" if image_path else None
            return ModelResult(id=match.group(1),source=self.key,title=item.get("name") or match.group(1),author=(item.get("user") or {}).get("publicUsername"),model_url=model_url,thumbnail_url=image,image_urls=[image] if image else [],available_files=files,raw_metadata={"graphql":True})
        except SourceError as exc:
            if not SCRAPLING_ENABLED:
                raise
            log.info("Printables GraphQL details failed (%s); retrying page with Scrapling", exc.code)
            return await ModelSource.get_model_details(self, model_url)

    async def get_downloads(self, model: ModelResult) -> list[DownloadableFile]:
        """Resolve Printables' file IDs to short-lived CDN URLs through GraphQL."""
        match=re.search(r"/model/(\d+)",urlparse(model.model_url).path)
        if not match:
            return model.available_files
        mutation="""mutation GetDownloadLink($id: ID!, $modelId: ID!, $fileType: DownloadFileTypeEnum!, $source: DownloadSourceEnum!) {
          getDownloadLink(id: $id, printId: $modelId, fileType: $fileType, source: $source) {
            ok output { link ttl } errors { field messages }
          }
        }"""
        # Printables stores 3MF projects in its `stls` collection too; the
        # download mutation identifies that collection as `stl` regardless of
        # the filename extension.
        file_types={".stl":"stl", ".3mf":"stl", ".gcode":"gcode", ".bgcode":"gcode"}
        for file in model.available_files:
            file_type=file_types.get(file.extension.lower())
            file_id=file.provider_file_id
            if not file_type or not file_id:
                continue
            try:
                payload=await self._graphql_with_profile(mutation,{"id":str(file_id),"modelId":match.group(1),"fileType":file_type,"source":"model_detail"})
                result=(payload.get("data") or {}).get("getDownloadLink") or {}
                url=(result.get("output") or {}).get("link") if result.get("ok") else None
                if url:
                    self.validate_download_url(url)
                    file.url=url
                    file.downloadable=True
                    file.reason=None
            except (SourceError, ValueError) as exc:
                file.reason=str(exc)
                log.info("Printables download URL unavailable for %s: %s",file.name,exc)
        return model.available_files

    async def _graphql_with_profile(self, query: str, variables: dict) -> dict:
        """Use cookies from the user's persistent browser profile for gated downloads."""
        from ..browser import BROWSER

        context=await BROWSER.context()
        cookies=await context.cookies(["https://www.printables.com", "https://api.printables.com"])
        cookie_header="; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies
            if cookie.get("domain", "").lstrip(".").endswith("printables.com"))
        await self._respect_rate()
        headers={
            "Content-Type":"application/json", "Origin":"https://www.printables.com",
            "Referer":"https://www.printables.com/",
            "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/134.0 Safari/537.36",
        }
        if cookie_header:
            headers["Cookie"]=cookie_header
        try:
            async with httpx.AsyncClient(timeout=20,follow_redirects=True) as client:
                response=await client.post("https://api.printables.com/graphql/",json={"query":query,"variables":variables},headers=headers)
        except httpx.TimeoutException as exc:
            raise SourceError("TIMEOUT","The Printables download-link request timed out.","graphql_authenticated") from exc
        except httpx.RequestError as exc:
            raise SourceError("NETWORK_ERROR",f"The Printables download-link endpoint could not be reached: {type(exc).__name__}.","graphql_authenticated") from exc
        if response.status_code in {401,403,429}:
            message={401:"Sign in to Printables in the saved browser profile to download this model.",403:"Printables rejected the download-link request. Check that the saved browser profile is signed in.",429:"Printables rate limit reached."}[response.status_code]
            raise SourceError(f"HTTP_{response.status_code}",message,"graphql_authenticated")
        try:
            response.raise_for_status()
            payload=response.json()
        except (httpx.HTTPStatusError,ValueError) as exc:
            raise SourceError("PARSE_ERROR","Printables returned an invalid download-link response.","graphql_authenticated") from exc
        if payload.get("errors"):
            message=str(payload["errors"][0].get("message") or "download-link request failed")
            raise SourceError("AUTH_REQUIRED",f"Printables could not authorize the download: {message}","graphql_authenticated")
        return payload

    async def _graphql(self, query: str, variables: dict) -> dict:
        """Request public listing metadata through Printables' GraphQL endpoint."""
        await self._respect_rate()
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response=await client.post("https://api.printables.com/graphql/",json={"query":query,"variables":variables},headers={
                    "Content-Type":"application/json", "Origin":"https://www.printables.com", "Referer":"https://www.printables.com/",
                    "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/134.0 Safari/537.36",
                })
        except httpx.TimeoutException as exc:
            raise SourceError("TIMEOUT","The Printables GraphQL request timed out.","graphql") from exc
        except httpx.RequestError as exc:
            raise SourceError("NETWORK_ERROR",f"The Printables GraphQL endpoint could not be reached: {type(exc).__name__}.","graphql") from exc
        if response.status_code in {401,403,429}:
            message={401:"Printables requires authentication.",403:"Printables rejected the GraphQL request.",429:"Printables rate limit reached."}[response.status_code]
            raise SourceError(f"HTTP_{response.status_code}",message,"graphql")
        try: response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SourceError(f"HTTP_{response.status_code}",f"Printables GraphQL returned HTTP {response.status_code}.","graphql") from exc
        try: payload=response.json()
        except ValueError as exc: raise SourceError("PARSE_ERROR","Printables GraphQL returned invalid JSON.","graphql") from exc
        if payload.get("errors"):
            raise SourceError("PARSE_ERROR",f"Printables GraphQL error: {payload['errors'][0].get('message','unknown error')}","graphql")
        return payload

    async def search(self, query: str, limit: int = 12) -> list[ModelResult]:
        self.ensure_enabled()
        gql="""query SearchModels($query: String!, $limit: Int!) {
          result: searchPrints2(query: $query, printType: print, limit: $limit, ordering: popular) {
            items { id name slug likesCount downloadCount ratingAvg image { filePath } user { publicUsername } }
          }
        }"""
        try:
            payload=await self._graphql(gql,{"query":query,"limit":limit})
        except SourceError as exc:
            if not SCRAPLING_ENABLED:
                raise
            log.info("Printables GraphQL search failed (%s); retrying page with Scrapling", exc.code)
            return await HTMLSearchSource.search(self, query, limit)
        items=payload.get("data",{}).get("result",{}).get("items",[])
        if not isinstance(items,list): raise SourceError("PARSE_ERROR","Printables GraphQL response did not include model results.","graphql")
        models=[]
        for index,item in enumerate(items[:limit],1):
            model_id=str(item.get("id") or ""); slug=str(item.get("slug") or "")
            if not model_id or not slug: continue
            image_path=(item.get("image") or {}).get("filePath")
            image_url=f"https://media.printables.com/{image_path.lstrip('/')}" if image_path else None
            downloads=item.get("downloadCount"); likes=item.get("likesCount")
            models.append(ModelResult(id=model_id,source=self.key,title=item.get("name") or f"Printables model {model_id}",
                author=(item.get("user") or {}).get("publicUsername"),model_url=f"https://www.printables.com/model/{model_id}-{slug}",
                thumbnail_url=image_url,image_urls=[image_url] if image_url else [],downloads=downloads,likes=likes,
                rating=float(item["ratingAvg"]) if item.get("ratingAvg") is not None else None,
                popularity_value=downloads if downloads is not None else likes,popularity_label="downloads" if downloads is not None else "likes" if likes is not None else None,
                raw_metadata={"ranking_position":index,"graphql":True}))
        models.sort(key=_popularity_key,reverse=True)
        self.last_attempts={"graphql":f"success ({len(models)} results)"};self.last_method="graphql";self.last_status="success_empty" if not models else "success";self.last_message=None
        self.last_parse_diagnostics={"results_discovered":len(items),"accepted":len(models),"missing_fields":self._missing(models)}
        return models

class MakerWorldSource(HTMLSearchSource):
    key="makerworld";label="MakerWorld";domains=("makerworld.com",);download_domains=("makerworld.bblmw.com",);search_method="api.bambulab.com"
    search_url="https://makerworld.com/en/search/models?keyword={query}&orderBy=6"
    def __init__(self):
        self._files_by_model_id={}
        self._profiles_by_model_id={}

    @staticmethod
    def _auth_headers():
        token=load_bambu_token()
        return {"Authorization":f"Bearer {token}"} if token else None

    def validate_download_url(self,url: str):
        parsed=urlparse(url)
        host=(parsed.hostname or "").lower()
        s3_host=host.endswith(".amazonaws.com") and (host.startswith("s3.") or ".s3." in host)
        if parsed.scheme!="https" or not host or not (self._host_allowed(host,self.domains+self.download_domains) or host=="model-file.bambulab.com" or s3_host):
            raise ValueError("URL is not an HTTPS MakerWorld download URL")

    async def _download_signed_profile(self,file: DownloadableFile,destination: Path):
        """Stream a short-lived signed Bambu URL without normalizing its signature query."""
        from urllib.error import HTTPError
        from urllib.request import HTTPRedirectHandler, Request, build_opener
        from ..config import HTTP_TIMEOUT_SECONDS, MAX_FILE_SIZE_MB

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None

        def fetch():
            self.validate_download_url(file.url or "")
            request=Request(file.url,headers={"User-Agent":"LocalModelFinder/1.0"})
            opener=build_opener(NoRedirect())
            total=0;limit=int(MAX_FILE_SIZE_MB*1024*1024)
            try:
                with opener.open(request,timeout=HTTP_TIMEOUT_SECONDS) as response:
                    content_type=response.headers.get("content-type","").split(";",1)[0].lower()
                    if "text/html" in content_type:raise ValueError("unexpected_content_type: MakerWorld returned HTML")
                    length=response.headers.get("content-length")
                    if length and int(length)>limit:raise ValueError("file_too_large")
                    with destination.open("wb") as out:
                        while True:
                            chunk=response.read(1024*1024)
                            if not chunk:break
                            total+=len(chunk)
                            if total>limit:raise ValueError("file_too_large")
                            out.write(chunk)
                if total<=0:raise ValueError("empty_download")
                return content_type,total
            except HTTPError as exc:
                raise ValueError(f"MakerWorld signed download returned HTTP {exc.code}") from exc
        try:
            content_type,total=await asyncio.to_thread(fetch)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            if not SCRAPLING_ENABLED:
                raise
            log.info("MakerWorld signed profile stream failed; retrying with Scrapling: %s", type(exc).__name__)
            return await super().download_file(file,destination)
        file.mime_type=content_type or None
        file.size_bytes=total
        return destination

    async def download_file(self,file: DownloadableFile,destination: Path):
        if file.provider_profile_id and file.url:
            return await self._download_signed_profile(file,destination)
        return await super().download_file(file,destination)

    async def download_browser_file(self,model_url: str,file: DownloadableFile,destination: Path):
        if not file.url and not load_bambu_token():
            raise SourceError(
                "AUTH_REQUIRED",
                "MakerWorld returned file metadata but no public download URL. Sign in to Bambu Cloud to resolve a downloadable print profile.",
                "api.bambulab.com",
            )
        return await super().download_browser_file(model_url,file,destination)

    def accept_url(self,url):return "/models/" in urlparse(url).path and bool(re.search(r"/\d+(?:-|$)",urlparse(url).path))

    async def get_model_details(self, model_url: str) -> ModelResult:
        """Use MakerWorld's public Bambu Cloud metadata API, not its 403-prone web page."""
        self.ensure_enabled()
        self.validate_url(model_url)
        match=re.search(r"/models/(\d+)",urlparse(model_url).path)
        if not match:
            return await super().get_model_details(model_url)
        try:
            response=await self._get_response(f"{BAMBU_API}/v1/design-service/design/{match.group(1)}",headers=self._auth_headers())
        except SourceError as exc:
            if not SCRAPLING_ENABLED:
                raise
            log.info("MakerWorld detail API failed (%s); retrying page with Scrapling",exc.code)
            return await ModelSource.get_model_details(self,model_url)
        try:
            item=response.json()
        except ValueError as exc:
            raise SourceError("PARSE_ERROR","MakerWorld's design API returned invalid JSON.","api.bambulab.com") from exc
        creator=item.get("designCreator") or {}
        cover=item.get("coverUrl")
        model_id=item.get("modelId")
        instances=item.get("instances") or []
        self._profiles_by_model_id[match.group(1)]={"model_id":model_id,"instances":instances}
        self.last_method="api.bambulab.com"
        return ModelResult(
            id=match.group(1),source=self.key,title=item.get("title") or match.group(1),
            author=creator.get("name"),model_url=model_url,thumbnail_url=cover,
            image_urls=[cover] if cover else [],downloads=item.get("downloadCount"),
            likes=item.get("likeCount"),description=item.get("summary"),license=item.get("license"),
            available_files=self._files_by_model_id.get(match.group(1),[]),
            raw_metadata={"api":"api.bambulab.com","model_id":model_id},
        )

    async def get_downloads(self,model: ModelResult) -> list[DownloadableFile]:
        """Use authorized direct file URLs when present; otherwise offer a signed profile 3MF."""
        token=load_bambu_token()
        if not token:
            return model.available_files
        direct_files=[file for file in model.available_files if file.downloadable and file.url]
        if direct_files:
            return direct_files
        cached=self._profiles_by_model_id.get(model.id) or {}
        internal_id=cached.get("model_id") or (model.raw_metadata or {}).get("model_id")
        instances=cached.get("instances") or []
        if not internal_id or not instances:
            return model.available_files
        instance=next((entry for entry in instances if entry.get("isDefault")),instances[0])
        profile_id=instance.get("profileId")
        if not profile_id:
            return model.available_files
        url=f"{BAMBU_API}/v1/iot-service/api/user/profile/{profile_id}"
        try:
            response=await self._get_response(url,params={"model_id":str(internal_id)},headers={"Authorization":f"Bearer {token}"})
            payload=response.json()
            signed_url=payload.get("url")
            if not signed_url:
                return model.available_files
            self.validate_download_url(signed_url)
            profile_name=instance.get("title") or "Default print profile"
            filename=f"{model.title} - {profile_name}.3mf"
            profile_file=DownloadableFile(name=filename,extension=".3mf",category="3MF",url=signed_url,
                downloadable=True,reason=None,size_bytes=None,provider_profile_id=str(profile_id),provider_model_id=str(internal_id))
            # MakerWorld's raw files remain login-gated through its challenged page.
            # Prefer a verified profile package instead of reporting every raw part as failed.
            return [profile_file]
        except (SourceError,ValueError,KeyError) as exc:
            log.info("MakerWorld profile download URL unavailable for %s: %s",model.id,exc)
            return model.available_files

    async def search(self, query: str, limit: int = 12) -> list[ModelResult]:
        """Search MakerWorld's public listing endpoint without loading its web page."""
        self.ensure_enabled()
        method="api.bambulab.com"
        self.last_method=method
        try:
            response=await self._get_response(
                "https://api.bambulab.com/v1/search-service/select/design2",
                params={"keyword":query,"limit":limit,"orderBy":6},
                headers=self._auth_headers(),
            )
            payload=response.json()
        except SourceError as exc:
            if not SCRAPLING_ENABLED:
                raise
            log.info("MakerWorld search API failed (%s); retrying page with Scrapling", exc.code)
            return await HTMLSearchSource.search(self, query, limit)
        except ValueError as exc:
            raise SourceError("PARSE_ERROR","MakerWorld's listing endpoint returned invalid JSON.",method) from exc

        items=payload.get("hits") if isinstance(payload,dict) else None
        if not isinstance(items,list):
            raise SourceError("PARSE_ERROR","MakerWorld's listing endpoint did not include model results.",method)

        models=[]
        for index,item in enumerate(items[:limit],1):
            if not isinstance(item,dict):
                continue
            model_id=str(item.get("id") or "")
            slug=str(item.get("slug") or "")
            if not model_id or not slug:
                continue
            files=[]
            for file in (item.get("designExtension") or {}).get("model_files") or []:
                if not isinstance(file,dict) or file.get("isDir"):
                    continue
                name=str(file.get("modelName") or file.get("modelFileName") or "")
                if not name:
                    continue
                extension=Path(name).suffix.lower() or f".{str(file.get('modelType') or 'file').lower()}"
                direct_url=str(file.get("modelUrl") or "")
                downloadable=False
                reason="MakerWorld's public response did not include a raw file URL."
                if direct_url:
                    try:
                        self.validate_download_url(direct_url)
                        downloadable=True;reason=None
                    except ValueError:
                        direct_url=""
                files.append(DownloadableFile(
                    name=name, extension=extension, category=category_for(name), size_bytes=file.get("modelSize"),
                    url=direct_url or None,downloadable=downloadable,reason=reason,
                ))
            pictures=(item.get("designExtension") or {}).get("design_pictures") or []
            image_urls=[picture.get("url") for picture in pictures if isinstance(picture,dict) and picture.get("url")]
            cover=item.get("cover")
            if cover and cover not in image_urls:
                image_urls.insert(0,cover)
            downloads=item.get("downloadCount"); likes=item.get("likeCount")
            models.append(ModelResult(
                id=model_id, source=self.key, title=item.get("title") or f"MakerWorld model {model_id}",
                author=(item.get("designCreator") or {}).get("name"),
                model_url=f"https://makerworld.com/en/models/{model_id}-{slug}", thumbnail_url=cover,
                image_urls=image_urls, downloads=downloads, likes=likes,
                popularity_value=downloads if downloads is not None else likes,
                popularity_label="downloads" if downloads is not None else "likes" if likes is not None else None,
                available_files=files, license=item.get("license"),
                raw_metadata={"ranking_position":index,"api":"api.bambulab.com"},
            ))
            self._files_by_model_id[model_id]=models[-1].available_files
        models.sort(key=_popularity_key,reverse=True)
        self.last_attempts={method:f"success ({len(models)} results)"};self.last_status="success_empty" if not models else "success";self.last_message=None
        self.last_parse_diagnostics={"results_discovered":len(items),"accepted":len(models),"missing_fields":self._missing(models)}
        return models
