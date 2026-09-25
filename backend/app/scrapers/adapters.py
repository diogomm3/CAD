import logging, re
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from bs4 import BeautifulSoup
import httpx
from .base import ModelSource, canonical_url, _meta, _author_from_soup
from .errors import SourceError
from ..models import DownloadableFile, ModelResult
from ..utils.filenames import category_for
from ..config import BROWSER_AUTH_ENABLED, PLAYWRIGHT_ENABLED, source_config

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
        methods=("http","playwright_public","playwright_authenticated")
        allowed={"auto":methods,"http":("http",),"browser":("playwright_public",),"authenticated_browser":("playwright_authenticated",)}.get(mode,())
        for method in methods:
            if method not in allowed:
                attempts[method]="not configured";continue
            if method!="http" and not PLAYWRIGHT_ENABLED:
                attempts[method]="Playwright is disabled"
                if mode!="auto":errors.append(SourceError("UNSUPPORTED","Playwright is disabled.",method))
                continue
            if method=="playwright_authenticated" and not (BROWSER_AUTH_ENABLED or mode=="authenticated_browser"):
                attempts[method]="not configured (set BROWSER_AUTH_ENABLED=true after manual login)";continue
            try:
                if method=="http": html=await self._get(url);self.last_method="http"
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
    key="printables";label="Printables";domains=("printables.com",);download_domains=("media.printables.com",);search_method="graphql"
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
                files.append(DownloadableFile(name=name,extension=Path(name).suffix.lower() or ".stl",category=category_for(name),size_bytes=item_file.get("fileSize"),downloadable=False,reason="Printables requires the normal browser download action."))
            image_path=(item.get("image") or {}).get("filePath")
            image=f"https://media.printables.com/{image_path.lstrip('/')}" if image_path else None
            return ModelResult(id=match.group(1),source=self.key,title=item.get("name") or match.group(1),author=(item.get("user") or {}).get("publicUsername"),model_url=model_url,thumbnail_url=image,image_urls=[image] if image else [],available_files=files,raw_metadata={"graphql":True})
        except SourceError:
            raise

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
        payload=await self._graphql(gql,{"query":query,"limit":limit})
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
    def accept_url(self,url):return "/models/" in urlparse(url).path and bool(re.search(r"/\d+(?:-|$)",urlparse(url).path))

    async def search(self, query: str, limit: int = 12) -> list[ModelResult]:
        """Search MakerWorld's public listing endpoint without loading its web page."""
        self.ensure_enabled()
        method="api.bambulab.com"
        self.last_method=method
        try:
            response=await self._get_response(
                "https://api.bambulab.com/v1/search-service/select/design2",
                params={"keyword":query,"limit":limit,"orderBy":6},
            )
            payload=response.json()
        except SourceError:
            raise
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
                files.append(DownloadableFile(
                    name=name, extension=extension, category=category_for(name), size_bytes=file.get("modelSize"),
                    downloadable=False, reason="MakerWorld did not provide a public direct download URL.",
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
        models.sort(key=_popularity_key,reverse=True)
        self.last_attempts={method:f"success ({len(models)} results)"};self.last_status="success_empty" if not models else "success";self.last_message=None
        self.last_parse_diagnostics={"results_discovered":len(items),"accepted":len(models),"missing_fields":self._missing(models)}
        return models
