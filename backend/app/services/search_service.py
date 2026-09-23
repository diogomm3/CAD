import asyncio
import logging
from ..scrapers.registry import SOURCES

log = logging.getLogger(__name__)

async def search_all(query: str, limit: int = 10):
    async def one(key, source):
        try:
            results = await source.search(query, limit)
            log.info("%s search returned %s results", key, len(results))
            return {"source": key, "label": source.label, "status": "success", "results": results[:limit], "error": None}
        except Exception as exc:
            log.warning("%s search failed: %s", key, exc)
            return {"source": key, "label": source.label, "status": "error", "results": [], "error": "Unable to search this source right now."}
    return await asyncio.gather(*(one(k, s) for k, s in SOURCES.items()))

