from fastapi import APIRouter
from ..schemas import SearchRequest
from ..services.search_service import search_all

router = APIRouter()

@router.post("/search")
async def search(request: SearchRequest):
    sources = await search_all(request.query)
    return {"query":request.query,"sources":sources,"total_results":sum(len(s["results"]) for s in sources)}
