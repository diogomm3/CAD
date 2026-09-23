import asyncio
import logging
from ..scrapers.registry import SOURCES
from ..scrapers.errors import SourceError
from ..config import MAX_CONCURRENT_SITES, SOURCE_SEARCH_TIMEOUT_SECONDS

log = logging.getLogger(__name__)

def _diagnostics(source):
    return {"access":getattr(source,"last_attempts",{}),"parse":getattr(source,"last_parse_diagnostics",{})}

async def search_all(query: str, limit: int = 10):
    semaphore=asyncio.Semaphore(MAX_CONCURRENT_SITES)
    async def one(key, source):
        try:
            async with semaphore: results = await asyncio.wait_for(source.search(query, limit),timeout=SOURCE_SEARCH_TIMEOUT_SECONDS)
            log.info("%s search returned %s results", key, len(results))
            status="success_empty" if not results else "success"
            source.last_status=status;source.last_message=None
            return {"source": key, "label": source.label, "status": status, "search_method": source.last_method, "diagnostics":_diagnostics(source), "results": results[:limit], "error": None}
        except asyncio.TimeoutError:
            source.last_status="timeout";source.last_message=f"Search exceeded {SOURCE_SEARCH_TIMEOUT_SECONDS:g} seconds."
            return {"source":key,"label":source.label,"status":"timeout","search_method":source.last_method or source.search_method,"diagnostics":_diagnostics(source),
                "results":[],"error":{"code":"TIMEOUT","message":source.last_message}}
        except SourceError as exc:
            source.last_status=exc.status;source.last_message=exc.message;source.last_method=exc.method or source.last_method
            log.warning("%s search %s (%s): %s", key, exc.status, exc.code, exc.message)
            return {"source":key,"label":source.label,"status":exc.status,"search_method":source.last_method,"diagnostics":_diagnostics(source),
                "results":[],"error":{"code":exc.code,"message":exc.message}}
        except Exception as exc:
            log.warning("%s search failed: %s", key, exc)
            source.last_status="network_error";source.last_message=f"{type(exc).__name__}"
            return {"source":key,"label":source.label,"status":"network_error","search_method":source.last_method,"diagnostics":_diagnostics(source),
                "results":[],"error":{"code":"UNEXPECTED_ERROR","message":"Unable to search this source right now."}}
    return await asyncio.gather(*(one(k, s) for k, s in SOURCES.items()))

async def search_source(query: str, key: str, limit: int = 10):
    if key not in SOURCES: raise KeyError(key)
    source=SOURCES[key]
    try:
        results=await asyncio.wait_for(source.search(query,limit),timeout=SOURCE_SEARCH_TIMEOUT_SECONDS)
        status="success_empty" if not results else "success"
        source.last_status=status;source.last_message=None
        return {"source":key,"label":source.label,"status":status,"search_method":source.last_method,"diagnostics":_diagnostics(source),"results":results,"error":None}
    except asyncio.TimeoutError:
        source.last_status="timeout";source.last_message=f"Search exceeded {SOURCE_SEARCH_TIMEOUT_SECONDS:g} seconds."
        return {"source":key,"label":source.label,"status":"timeout","search_method":source.last_method or source.search_method,"diagnostics":_diagnostics(source),"results":[],"error":{"code":"TIMEOUT","message":source.last_message}}
    except SourceError as exc:
        source.last_status=exc.status;source.last_message=exc.message;source.last_method=exc.method or source.last_method
        return {"source":key,"label":source.label,"status":exc.status,"search_method":source.last_method,"diagnostics":_diagnostics(source),"results":[],
                "error":{"code":exc.code,"message":exc.message}}

def source_diagnostics():
    output=[]
    for key,source in SOURCES.items():
        status,message=source.last_status,source.last_message
        if key=="thingiverse":
            from ..config import THINGIVERSE_API_KEY
            if not THINGIVERSE_API_KEY and status=="ready":status,message="authentication_required","Set THINGIVERSE_API_KEY to use the documented Thingiverse API."
        item={"id":key,"name":source.label,"enabled":True,"search_method":source.last_method or source.search_method,
            "status":status,"message":message,"diagnostics":_diagnostics(source)}
        if key=="thingiverse":
            from ..config import THINGIVERSE_API_KEY
            item["api_key"]="configured" if THINGIVERSE_API_KEY else "not_configured"
            item["api"]="reachable" if source.last_status in {"success","success_empty"} else "unavailable" if source.last_status not in {"ready","authentication_required"} else "not_checked"
            item["search"]="available" if source.last_status in {"success","success_empty"} else "unavailable" if source.last_status not in {"ready","authentication_required"} else "authentication_required" if not THINGIVERSE_API_KEY else "not_checked"
        output.append(item)
    return output
