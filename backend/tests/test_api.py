import httpx
import pytest
from app.main import app
from app.routes import search as search_route

@pytest.mark.asyncio
async def test_health_endpoint():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_search_returns_each_source_status(monkeypatch):
    async def fake_search(query):
        return [{"source":"printables","label":"Printables","status":"error","results":[],"error":"unavailable"}]
    monkeypatch.setattr(search_route, "search_all", fake_search)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/search", json={"query":"Thor Hammer"})
    assert response.status_code == 200
    assert response.json()["sources"][0]["status"] == "error"

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
