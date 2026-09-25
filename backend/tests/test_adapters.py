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
async def test_graphql_403_is_reported_as_blocked(monkeypatch):
    adapter=PrintablesSource()
    async def denied(query,variables):raise SourceError("HTTP_403","Printables rejected the GraphQL request.","graphql")
    monkeypatch.setattr(adapter,"_graphql",denied)
    with pytest.raises(SourceError) as error:await adapter.search("thor hammer")
    assert error.value.status=="blocked"
    assert error.value.code=="HTTP_403"

@pytest.mark.asyncio
async def test_printables_graphql_search_normalizes_result_cards(monkeypatch):
    adapter=PrintablesSource()
    async def graphql(query,variables):
        assert variables=={"query":"thor hammer","limit":10}
        return {"data":{"result":{"items":[{"id":"447061","slug":"thor-hammer-bookend","name":"Thor hammer bookend","likesCount":144,"downloadCount":934,"ratingAvg":"4.5","image":{"filePath":"media/prints/447061/cover.jpg"},"user":{"publicUsername":"PurpxHaze91"}}]}}}
    monkeypatch.setattr(adapter,"_graphql",graphql)
    results=await adapter.search("thor hammer")
    assert len(results)==1
    assert results[0].id=="447061"
    assert results[0].model_url=="https://www.printables.com/model/447061-thor-hammer-bookend"
    assert results[0].thumbnail_url=="https://media.printables.com/media/prints/447061/cover.jpg"
    assert results[0].downloads==934 and results[0].author=="PurpxHaze91"

@pytest.mark.asyncio
async def test_makerworld_api_search_normalizes_cards_and_file_metadata(monkeypatch):
    adapter=MakerWorldSource()
    class Response:
        def json(self):
            return {"hits":[{"id":42372,"slug":"thor-hammer","title":"Thor Hammer","cover":"https://makerworld.bblmw.com/cover.jpg","likeCount":1603,"downloadCount":5334,"license":"CC0","designCreator":{"name":"sWc Creation"},"designExtension":{"design_pictures":[{"url":"https://makerworld.bblmw.com/second.jpg"}],"model_files":[{"modelName":"body.stl","modelSize":1226484,"modelType":"stl","modelUrl":""},{"modelName":"plate.3mf","modelSize":105684,"modelType":"3mf","modelUrl":""}]}}]}
    async def get_response(url,*,params=None,headers=None):
        assert url=="https://api.bambulab.com/v1/search-service/select/design2"
        assert params=={"keyword":"thor hammer","limit":10}
        return Response()
    monkeypatch.setattr(adapter,"_get_response",get_response)
    results=await adapter.search("thor hammer")
    assert len(results)==1
    model=results[0]
    assert model.model_url=="https://makerworld.com/en/models/42372-thor-hammer"
    assert model.author=="sWc Creation" and model.downloads==5334 and model.license=="CC0"
    assert model.image_urls==["https://makerworld.bblmw.com/cover.jpg","https://makerworld.bblmw.com/second.jpg"]
    assert [(file.name,file.category,file.downloadable) for file in model.available_files]==[("body.stl","STL",False),("plate.3mf","3MF",False)]

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
    async def graphql(query,variables):return {"data":{"result":{"items":[{"id":"101","slug":"fixture-hammer","name":"Fixture Hammer","user":{}}]}}}
    async def browser(*args,**kwargs):raise AssertionError("browser must not be used in http mode")
    monkeypatch.setattr(adapter,"_graphql",graphql)
    monkeypatch.setattr(adapter,"_browser_html",browser)
    result=await adapter.search("hammer")
    assert len(result)==1
