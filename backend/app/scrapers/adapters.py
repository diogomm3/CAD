from urllib.parse import quote_plus, urljoin
from bs4 import BeautifulSoup
from .base import ModelSource
from ..models import ModelResult

class HTMLSearchSource(ModelSource):
    async def search(self, query: str, limit: int = 10) -> list[ModelResult]:
        html = await self._get(self.search_url.format(query=quote_plus(query)))
        soup = BeautifulSoup(html, "lxml")
        seen, results = set(), []
        for a in soup.select("a[href]"):
            href = urljoin(self.search_url, a.get("href", ""))
            if not any(d in (href.split("/")[2] if "://" in href else "") for d in self.domains): continue
            title = a.get("title") or a.get_text(" ", strip=True)
            if not title or len(title) < 3: continue
            if not self.accept_url(href) or href in seen: continue
            seen.add(href)
            img = a.select_one("img")
            results.append(ModelResult(id=href.rstrip("/").split("/")[-1], source=self.key,
                title=title[:240], author=None, model_url=href,
                thumbnail_url=(urljoin(href, img.get("src") or img.get("data-src")) if img and (img.get("src") or img.get("data-src")) else None),
                image_urls=[]))
            if len(results) >= limit: break
        return results

    def accept_url(self, url: str) -> bool: return True

class PrintablesSource(HTMLSearchSource):
    key="printables"; label="Printables"; domains=("printables.com",)
    search_url="https://www.printables.com/search/models?q={query}"
    def accept_url(self, url): return "/model/" in url

class MakerWorldSource(HTMLSearchSource):
    key="makerworld"; label="MakerWorld"; domains=("makerworld.com",)
    search_url="https://makerworld.com/en/search/models?keyword={query}"
    def accept_url(self, url): return "/models/" in url

class ThingiverseSource(HTMLSearchSource):
    key="thingiverse"; label="Thingiverse"; domains=("thingiverse.com",)
    search_url="https://www.thingiverse.com/search?q={query}&type=things&sort=relevant"
    def accept_url(self, url): return "/thing:" in url or "/thing/" in url

class GrabCADSource(HTMLSearchSource):
    key="grabcad"; label="GrabCAD"; domains=("grabcad.com",)
    search_url="https://grabcad.com/library?query={query}"
    def accept_url(self, url): return "/library/" in url

