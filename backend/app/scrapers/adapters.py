import logging, re
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from bs4 import BeautifulSoup
from .base import ModelSource, canonical_url, _meta, _author_from_soup
from .errors import SourceError
from ..models import ModelResult
from ..utils.filenames import category_for
from ..config import BROWSER_AUTH_ENABLED, PLAYWRIGHT_ENABLED, source_config

log = logging.getLogger(__name__)

def _number(value: str | None) -> int | None:
    if not value: return None
    value=value.strip().lower().replace(",", "")
    match=re.search(r"(\d+(?:\.\d+)?)\s*([km]?)", value)
    if not match:return None
    return int(float(match.group(1)) * {"k":1000,"m":1000000,"":1}[match.group(2)])

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

    async def search(self, query: str, limit: int = 10) -> list[ModelResult]:
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
    key="printables";label="Printables";domains=("printables.com",);download_domains=("media.printables.com",);search_method="http+playwright"
    search_url="https://www.printables.com/search/models?q={query}&o=popular"
    def accept_url(self,url):return bool(re.search(r"/model/\d+",urlparse(url).path))

class MakerWorldSource(HTMLSearchSource):
    key="makerworld";label="MakerWorld";domains=("makerworld.com",);download_domains=("makerworld.bblmw.com",);search_method="http+playwright"
    search_url="https://makerworld.com/en/search/models?keyword={query}&orderBy=6"
    def accept_url(self,url):return "/models/" in urlparse(url).path and bool(re.search(r"/\d+(?:-|$)",urlparse(url).path))

class GrabCADSource(HTMLSearchSource):
    key="grabcad";label="GrabCAD";domains=("grabcad.com",);search_method="http+playwright"
    search_url="https://grabcad.com/library?query={query}&sort=popular"
    def accept_url(self,url):return "/library/" in urlparse(url).path and urlparse(url).path!="/library/"

class ThingiverseSource(ModelSource):
    key="thingiverse";label="Thingiverse";domains=("thingiverse.com",);download_domains=("cdn.thingiverse.com",);search_method="api"
    api="https://api.thingiverse.com"

    def _auth(self):
        from ..config import THINGIVERSE_API_KEY
        if not THINGIVERSE_API_KEY: raise SourceError("API_KEY_REQUIRED","Thingiverse API token is not configured; set THINGIVERSE_API_KEY.","api")
        return {"Authorization":f"Bearer {THINGIVERSE_API_KEY}"}

    async def _api_json(self,path,params=None):
        try:response=await self._get_response(f"{self.api}{path}",params=params,headers=self._auth())
        except SourceError as exc:
            raise SourceError(exc.code,exc.message,"api") from exc
        try:
            data=response.json()
            from ..config import DEBUG_SCRAPERS
            if DEBUG_SCRAPERS:self.save_debug(response.text,"-api.json")
            return data
        except ValueError as exc:raise SourceError("PARSE_JSON","Thingiverse API returned invalid JSON.","api") from exc

    async def search(self,query: str,limit: int=10)->list[ModelResult]:
        self.ensure_enabled()
        if source_config(self.key)["access_mode"] not in {"auto","api"}:
            raise SourceError("UNSUPPORTED","Thingiverse access mode must be api or auto.","api")
        self.last_attempts={"api":"pending"}
        try:data=await self._api_json("/search/"+quote_plus(query),{"type":"things","sort":"popular","per_page":limit})
        except SourceError as exc:
            self.last_attempts["api"]=f"{exc.code}: {exc.message}"
            raise
        items=data if isinstance(data,list) else data.get("hits",data.get("things",data.get("results",[]))) if isinstance(data,dict) else []
        models=[]
        for item in items[:limit]:
            thing_id=str(item.get("id") or item.get("thing_id") or "")
            if not thing_id:continue
            models.append(self._normalize(item,thing_id))
        log.info("ThingiverseParser: API returned %d candidates; normalized %d",len(items),len(models))
        self.last_attempts["api"]=f"success ({len(models)} results)"
        self.last_status="success_empty" if not models else "success";self.last_message=None
        return models

    def _normalize(self,item,thing_id=None):
        thing_id=str(item.get("id") or thing_id or "")
        url=canonical_url(item.get("public_url") or item.get("url") or f"https://www.thingiverse.com/thing:{thing_id}")
        creator=item.get("creator") or item.get("user") or {}
        if isinstance(creator,str):author=creator
        else:author=creator.get("name") or creator.get("username") or creator.get("full_name")
        default_image=item.get("default_image") or {}
        thumb=item.get("thumbnail") or item.get("thumbnail_url") or (default_image.get("url") if isinstance(default_image,dict) else None)
        likes=item.get("like_count") or item.get("likes") or item.get("collect_count")
        downloads=item.get("download_count") or item.get("downloads")
        return ModelResult(id=thing_id,source=self.key,title=item.get("name") or item.get("title") or f"Thing {thing_id}",author=author,
          model_url=url,thumbnail_url=thumb,image_urls=[thumb] if thumb else [],likes=int(likes) if likes is not None else None,
          downloads=int(downloads) if downloads is not None else None,popularity_value=int(downloads or likes) if downloads or likes else None,
          popularity_label="downloads" if downloads is not None else "likes" if likes is not None else None,
          description=item.get("description"),license=(item.get("license") or {}).get("name") if isinstance(item.get("license"),dict) else item.get("license"),raw_metadata=item)

    async def get_model_details(self,model_url: str)->ModelResult:
        self.ensure_enabled()
        if source_config(self.key)["access_mode"] not in {"auto","api"}:
            raise SourceError("UNSUPPORTED","Thingiverse access mode must be api or auto.","api")
        self.validate_url(model_url)
        match=re.search(r"thing:(\d+)",model_url) or re.search(r"/things/(\d+)",model_url)
        if not match:raise ValueError("Could not identify Thingiverse model id")
        item=await self._api_json(f"/things/{match.group(1)}")
        return self._normalize(item,match.group(1))

    async def get_downloads(self,model: ModelResult):
        data=model.raw_metadata or await self._api_json(f"/things/{model.id}")
        raw=data.get("files") or await self._api_json(f"/things/{model.id}/files")
        files=[]
        if isinstance(raw,dict):raw=raw.get("files",[raw] if raw.get("name") else [])
        for item in raw if isinstance(raw,list) else []:
            name=item.get("name") or item.get("filename") or ""
            ext=Path(name).suffix.lower()
            if ext in {".gcode",".bgcode"}:continue
            url=item.get("url") or item.get("download_url") or item.get("public_url")
            from urllib.parse import urlparse as parse_url
            parsed=parse_url(url or "")
            downloadable=bool(url and parsed.scheme=="https" and self._host_allowed(parsed.hostname,self.domains+self.download_domains))
            normalized_name=name or f"file-{item.get('id')}{ext}"
            files.append({"name":normalized_name,"original_name":normalized_name,"extension":ext,"category":category_for(normalized_name),"url":url,"source_url":url,"downloadable":downloadable,"reason":None if downloadable else "download url not provided or host could not be validated"})
        from ..models import DownloadableFile
        return [DownloadableFile(**f) for f in files]
