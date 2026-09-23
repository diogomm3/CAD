import httpx
import pytest
import asyncio
import json
from pathlib import Path
from app.main import app
from app.routes import search as search_route, projects as projects_route
from app.models import DownloadableFile, ModelResult

@pytest.mark.asyncio
async def test_health_endpoint():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_search_returns_each_source_status(monkeypatch):
    async def fake_search(query):
        return [{"source":"printables","label":"Printables","status":"blocked","results":[],"error":"unavailable"}]
    monkeypatch.setattr(search_route, "search_all", fake_search)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/search", json={"query":"Thor Hammer"})
    assert response.status_code == 200
    assert response.json()["sources"][0]["status"] == "blocked"

@pytest.mark.asyncio
async def test_manual_import_rejects_unrecognized_hosts():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.post("/api/models/import",json={"source":"printables","model_url":"https://example.com/model/12-test"})
    assert response.status_code==422

@pytest.mark.asyncio
async def test_search_select_project_download_workflow_writes_manifest(tmp_path,monkeypatch):
    from app.services import download_service
    file_url="https://media.printables.com/hammer.stl"
    asset=DownloadableFile(name="hammer.stl",extension=".stl",category="STL",url=file_url,downloadable=True)
    model=ModelResult(id="101-thor-hammer",source="printables",title="Thor Hammer",model_url="https://www.printables.com/model/101-thor-hammer")
    class FakeSource:
        key="printables";label="Printables";domains=("printables.com",);download_domains=("media.printables.com",)
        config={"enabled":True,"access_mode":"auto"}
        def validate_url(self,url):pass
        def validate_download_url(self,url):pass
        async def get_model_details(self,url):return model.model_copy(update={"available_files":[asset]})
        async def get_downloads(self,detail):return detail.available_files
        async def download_file(self,file,destination):
            destination.write_text("solid hammer\n facet normal 0 0 0\n outer loop\n vertex 0 0 0\n vertex 1 0 0\n vertex 0 1 0\n endloop\n endfacet\n endsolid hammer\n")
            return destination
    monkeypatch.setitem(projects_route.SOURCES,"printables",FakeSource())
    monkeypatch.setattr(projects_route,"PROJECTS_DIR",tmp_path)
    monkeypatch.setattr(download_service,"PROJECTS_DIR",tmp_path)
    async def fake_search(query):
        return [{"source":"printables","label":"Printables","status":"success","results":[model],"error":None}]
    monkeypatch.setattr(search_route,"search_all",fake_search)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        found=await client.post("/api/search",json={"query":"Thor Hammer"})
        assert found.status_code==200 and found.json()["total_results"]==1
        started=await client.post("/api/projects",json={"query":"Thor Hammer","models":[{"source":"printables","model_id":model.id,"model_url":model.model_url}]})
        assert started.status_code==202
        job_id=started.json()["job_id"]
        state=started.json()
        for _ in range(50):
            await asyncio.sleep(.01)
            state=(await client.get(f"/api/projects/{job_id}")).json()
            if state["status"] in {"completed","partial","failed"}:break
    version=tmp_path/"Thor_Hammer"/"v1"
    assert state["status"]=="completed"
    assert (version/"STL"/"hammer.stl").is_file()
    assert all((version/name).is_dir() for name in ("Image","STL","3MF","CAD","Source"))
    metadata=json.loads((version/"metadata.json").read_text())
    assert {"original_name","local_path","category","extension","mime_type","size_bytes","sha256","source_url"}<=metadata["files"][0].keys()
    assert metadata["files"][0]["sha256"] and len(metadata["files"][0]["sha256"])==64
    assert state["project_name"]=="Thor_Hammer" and state["version"]=="v1" and state["path"]=="Thor_Hammer/v1"
    manifest=json.loads((tmp_path/"Thor_Hammer"/"project.json").read_text())
    assert manifest["versions"][0]["source"]=="Printables"

@pytest.mark.asyncio
async def test_search_all_keeps_working_source_when_another_is_blocked(monkeypatch):
    from app.services import search_service
    from app.scrapers.errors import SourceError
    class Good:
        label="GrabCAD";search_method="http"
        last_method=None;last_status="ready";last_message=None
        async def search(self,query,limit):return [ModelResult(id="1",source="grabcad",title="Model",model_url="https://grabcad.com/library/1-model")]
    class Blocked:
        label="Printables";search_method="http"
        last_method=None;last_status="ready";last_message=None
        async def search(self,query,limit):raise SourceError("HTTP_403","Source returned HTTP 403.","http")
    monkeypatch.setattr(search_service,"SOURCES",{"grabcad":Good(),"printables":Blocked()})
    results=await search_service.search_all("hammer")
    assert [(item["source"],item["status"],len(item["results"])) for item in results]==[("grabcad","success",1),("printables","blocked",0)]

@pytest.mark.asyncio
async def test_source_status_diagnostics():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response=await client.get("/api/sources/status")
    assert response.status_code==200
    sources=response.json()["sources"]
    assert [source["id"] for source in sources]==["printables","makerworld","thingiverse","grabcad"]
    assert sources[2]["search_method"]=="api"
    assert sources[2]["api_key"] in {"configured","not_configured"}
    assert sources[2]["api_key"] != "fixture-token"
