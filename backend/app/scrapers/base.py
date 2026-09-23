from abc import ABC, abstractmethod
from urllib.parse import urlparse, urljoin
import httpx
from bs4 import BeautifulSoup
from ..models import ModelResult
from ..models import DownloadableFile
from ..utils.filenames import category_for
from ..config import HTTP_TIMEOUT_SECONDS

class ModelSource(ABC):
    key: str
    label: str
    domains: tuple[str, ...]
    search_url: str

    async def _get(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LocalModelFinder/1.0; personal use)"}) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[ModelResult]: ...

    async def get_model_details(self, model_url: str) -> ModelResult:
        self.validate_url(model_url)
        html = await self._get(model_url)
        return self.parse_detail(html, model_url)

    async def get_downloads(self, model: ModelResult):
        detail = await self.get_model_details(model.model_url)
        return detail.available_files

    async def download_file(self, file, destination):
        self.validate_url(file.url or "")
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LocalModelFinder/1.0)"}) as client:
            async with client.stream("GET", file.url) as response:
                response.raise_for_status()
                with destination.open("wb") as out:
                    async for chunk in response.aiter_bytes(): out.write(chunk)
        return destination

    def validate_url(self, url: str):
        p = urlparse(url)
        if p.scheme != "https" or not any(p.hostname == d or (p.hostname or "").endswith("." + d) for d in self.domains):
            raise ValueError("URL is not an HTTPS URL belonging to this source")

    def parse_detail(self, html: str, url: str) -> ModelResult:
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.get_text(" ", strip=True) if soup.title else url
        image = soup.find("meta", property="og:image")
        desc = soup.find("meta", attrs={"name": "description"})
        files = []
        for a in soup.select("a[href]"):
            href = urlparse(urljoin(url, a.get("href", "")))
            name = (a.get("download") or href.path.rstrip("/").split("/")[-1])
            if not name or "." not in name: continue
            ext = "." + name.rsplit(".", 1)[-1].lower()
            if ext not in {".stl", ".3mf", ".step", ".stp", ".iges", ".igs", ".sldprt", ".sldasm", ".f3d", ".scad", ".blend", ".fcstd", ".zip"}: continue
            absolute = href.geturl()
            ok = href.scheme == "https" and any(href.hostname == d or (href.hostname or "").endswith("." + d) for d in self.domains)
            files.append(DownloadableFile(name=name, extension=ext, category=category_for(name), url=absolute,
                downloadable=ok, reason=None if ok else "download host could not be validated"))
        return ModelResult(id=url.rstrip("/").split("/")[-1], source=self.key, title=title,
            model_url=url, thumbnail_url=image.get("content") if image else None,
            image_urls=[image["content"]] if image and image.get("content") else [],
            available_files=files, description=desc.get("content") if desc else None)
