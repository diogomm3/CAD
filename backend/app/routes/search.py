from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ..schemas import SearchRequest
from ..services.search_service import search_all, search_source, source_diagnostics
from ..scrapers.registry import SOURCES

router = APIRouter()

@router.post("/search")
async def search(request: SearchRequest):
    sources = await search_all(request.query)
    return {"query":request.query,"sources":sources,"total_results":sum(len(s["results"]) for s in sources)}

class SourceSearchRequest(BaseModel):
    query: str = Field(min_length=1,max_length=120)
    source: str

@router.post("/search/source")
async def retry_source(request: SourceSearchRequest):
    if request.source not in SOURCES: raise HTTPException(404,"Unknown source")
    try:return await search_source(request.query.strip(),request.source)
    except KeyError as exc:raise HTTPException(404,"Unknown source") from exc

@router.get("/sources/status")
async def sources_status(): return {"sources":source_diagnostics()}
