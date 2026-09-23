from pathlib import Path
import pytest
from app.scrapers.adapters import PrintablesSource, MakerWorldSource, GrabCADSource
from app.scrapers.base import canonical_url
from app.scrapers.errors import SourceError
from app.config import SOURCE_CONFIG

FIXTURES=Path(__file__).parent/"fixtures"
def read(source,name):return (FIXTURES/source/name).read_text(encoding="utf-8")

@pytest.mark.parametrize("adapter,source,expected_id,expected_title,expected_author,expected_metric",[
    (PrintablesSource(),"printables","101-fixture-hammer","Fixture Thor Hammer","NorseMaker",("downloads",240)),
    (MakerWorldSource(),"makerworld","202-fixture-hammer","Maker Hammer","CADViking",("downloads",4900)),
    (GrabCADSource(),"grabcad","303-fixture-hammer","Thor CAD Assembly","EngineerOne",("likes",321)),
])
def test_html_search_fixtures_parse_normalized_metadata(adapter,source,expected_id,expected_title,expected_author,expected_metric):
    result=adapter._candidate_models(read(source,"search.html"),10)
    assert len(result)==1
    model=result[0]
    assert model.id==expected_id
    assert model.title==expected_title
    assert model.author==expected_author
    assert model.model_url.startswith("https://")
    assert model.thumbnail_url
    assert getattr(model,expected_metric[0])==expected_metric[1]

@pytest.mark.parametrize("adapter,source,expected",[
    (PrintablesSource(),"printables",{"STL":1,"3MF":1,"OTHER":1}),
    (MakerWorldSource(),"makerworld",{"STL":1,"3MF":1,"SOURCE":1}),
    (GrabCADSource(),"grabcad",{"CAD":2,"SOURCE":2}),
])
def test_html_model_fixtures_parse_all_supported_files(adapter,source,expected):
    model=adapter.parse_detail(read(source,"model.html"),{"printables":"https://www.printables.com/model/101-fixture-hammer","makerworld":"https://makerworld.com/en/models/202-fixture-hammer","grabcad":"https://grabcad.com/library/303-fixture-hammer"}[source])
    assert model.title
    assert model.author
    assert model.thumbnail_url
    counts={kind:sum(file.category==kind for file in model.available_files) for kind in {file.category for file in model.available_files}}
    assert counts==expected
    assert all(file.downloadable for file in model.available_files)
    assert not any(file.extension in {".gcode",".bgcode"} for file in model.available_files)

@pytest.mark.asyncio
async def test_http_403_remains_blocked_when_browser_runtime_is_missing(monkeypatch):
    adapter=PrintablesSource()
    async def denied(url):raise SourceError("HTTP_403","Source returned HTTP 403.","http")
    async def browser_error(url,authenticated=False):raise SourceError("BROWSER_UNAVAILABLE","Browser unavailable.","playwright_public")
    monkeypatch.setattr(adapter,"_get",denied)
    monkeypatch.setattr(adapter,"_browser_html",browser_error)
    with pytest.raises(SourceError) as error:await adapter.search("thor hammer")
    assert error.value.status=="blocked"
    assert error.value.code=="HTTP_403"

def test_canonical_url_removes_tracking_and_keeps_identity_query():
    assert canonical_url("/model/42?variant=3&utm_source=mail","https://www.printables.com/search") == "https://www.printables.com/model/42?variant=3"

def test_html_detail_ignores_gcode_and_preserves_filename():
    source=PrintablesSource()
    detail=source.parse_detail('<html><head><meta property="og:title" content="Model"></head><body><a href="/files/a.stl">custom-name.stl</a><a href="/files/a.gcode">a.gcode</a></body></html>',"https://www.printables.com/model/1-test")
    assert [f.name for f in detail.available_files]==["a.stl"]
    assert detail.available_files[0].category=="STL"

@pytest.mark.asyncio
async def test_source_can_be_disabled_without_contacting_website(monkeypatch):
    monkeypatch.setitem(SOURCE_CONFIG["printables"],"enabled",False)
    with pytest.raises(SourceError) as error:await PrintablesSource().search("hammer")
    assert error.value.status=="unsupported"
    assert error.value.code=="SOURCE_DISABLED"

@pytest.mark.asyncio
async def test_http_mode_does_not_start_browser(monkeypatch):
    adapter=PrintablesSource()
    monkeypatch.setitem(SOURCE_CONFIG["printables"],"access_mode","http")
    async def html(url):return read("printables","search.html")
    async def browser(*args,**kwargs):raise AssertionError("browser must not be used in http mode")
    monkeypatch.setattr(adapter,"_get",html)
    monkeypatch.setattr(adapter,"_browser_html",browser)
    result=await adapter.search("hammer")
    assert len(result)==1
