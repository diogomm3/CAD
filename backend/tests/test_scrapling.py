import httpx
import pytest

from app.models import DownloadableFile
from app.scrapers import base
from app.scrapers.adapters import HTMLSearchSource, PrintablesSource
from app.scrapers.scrapling_client import ScraplingResponse
from app.scrapers.errors import SourceError


def test_scrapling_response_decodes_body_instead_of_selector_text():
    response = ScraplingResponse(
        url="https://example.com/", status=200, headers={"content-type": "text/html"},
        body="<h1>Þór</h1>".encode(), encoding="utf-8",
    )

    assert response.text == "<h1>Þór</h1>"


@pytest.mark.asyncio
async def test_search_falls_back_to_scrapling_after_http_block(monkeypatch):
    class FixtureSource(HTMLSearchSource):
        key = "printables"
        label = "Printables"
        domains = ("printables.com",)
        search_url = "https://www.printables.com/search/models?q={query}"

    source = FixtureSource()
    monkeypatch.setitem(source.config, "access_mode", "auto")

    async def blocked(_url):
        raise base.SourceError("HTTP_403", "blocked", "http")

    async def fetched(_url):
        return ScraplingResponse(
            url="https://www.printables.com/search/models?q=hammer", status=200,
            headers={"content-type": "text/html"},
            body=b'<article><a href="/model/101-fixture-hammer"><h3>Fixture Hammer</h3><img src="https://media.printables.com/101.jpg"></a><span itemprop="author">NorseMaker</span></article>',
            encoding="utf-8",
        )

    monkeypatch.setattr(source, "_get", blocked)
    monkeypatch.setattr(source, "_scrapling_fetch", fetched)

    models = await source.search("hammer", limit=5)

    assert len(models) == 1
    assert models[0].title == "Fixture Hammer"
    assert source.last_method == "scrapling"


@pytest.mark.asyncio
async def test_scrapling_page_does_not_treat_hidden_challenge_script_as_block(monkeypatch):
    source=PrintablesSource()
    async def fetched(_url,**kwargs):
        return ScraplingResponse(
            url="https://www.printables.com/",status=200,headers={},
            body=b'<html><script>window.captchaProvider="turnstile"</script><body><h1>Models</h1></body></html>',
            encoding="utf-8",
        )
    monkeypatch.setattr(source,"_scrapling_fetch",fetched)

    assert "Models" in await source._scrapling_html("https://www.printables.com/")


@pytest.mark.asyncio
async def test_file_download_retries_with_scrapling_and_checks_final_domain(monkeypatch, tmp_path):
    source = PrintablesSource()
    monkeypatch.setattr(base, "SCRAPLING_ENABLED", True)

    class FailedStream:
        async def __aenter__(self):
            request = httpx.Request("GET", "https://files.printables.com/model.stl")
            raise httpx.ConnectError("challenge", request=request)

        async def __aexit__(self, *args):
            return None

    class FailedClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, *args, **kwargs):
            return FailedStream()

    monkeypatch.setattr(base.httpx, "AsyncClient", lambda **kwargs: FailedClient())
    file = DownloadableFile(
        name="model.stl", extension=".stl", category="STL",
        url="https://files.printables.com/model.stl", downloadable=True,
    )

    async def scrapling_ok(_url, **kwargs):
        class Response:
            url="https://files.printables.com/model.stl"
            status=200
            headers={"content-type":"application/octet-stream"}
            async def body(self):return b"solid model"
        class Page:
            class Context:
                class Request:
                    async def get(self,*args,**kwargs):return Response()
                request=Request()
            context=Context()
        await kwargs["page_action"](Page())

    monkeypatch.setattr(source, "_scrapling_fetch", scrapling_ok)
    destination = tmp_path / "model.stl"
    await source.download_file(file, destination)
    assert destination.read_bytes() == b"solid model"
    assert file.size_bytes == len(b"solid model")

    async def scrapling_offsite(_url, **kwargs):
        class Response:
            url="https://not-printables.example/model.stl"
            status=200
            headers={"content-type":"application/octet-stream"}
            async def body(self):return b"solid model"
        class Page:
            class Context:
                class Request:
                    async def get(self,*args,**kwargs):return Response()
                request=Request()
            context=Context()
        await kwargs["page_action"](Page())

    monkeypatch.setattr(source, "_scrapling_fetch", scrapling_offsite)
    destination.unlink()
    with pytest.raises(ValueError, match="download URL"):
        await source.download_file(file, destination)
    assert not destination.exists()


@pytest.mark.asyncio
async def test_scrapling_browser_context_downloads_file(monkeypatch, tmp_path):
    source=PrintablesSource()
    file=DownloadableFile(
        name="model.stl",extension=".stl",category="STL",
        url="https://files.printables.com/model.stl",downloadable=True,
    )
    destination=tmp_path/"model.stl"

    class Response:
        url=file.url
        status=200
        headers={"content-type":"application/octet-stream"}
        async def body(self):return b"solid model"

    class Page:
        class Context:
            class Request:
                async def get(self,url,timeout):return Response()
            request=Request()
        context=Context()

    async def fetched(url,*,page_action,**kwargs):
        assert url=="https://www.printables.com"
        await page_action(Page())

    monkeypatch.setattr(source,"_scrapling_fetch",fetched)
    content_type,size=await source._scrapling_download_file(file,destination)

    assert destination.read_bytes()==b"solid model"
    assert content_type=="application/octet-stream"
    assert size==len(b"solid model")
