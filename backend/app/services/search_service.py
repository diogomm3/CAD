import asyncio
import logging
from datetime import datetime, timezone
from ..scrapers.registry import SOURCES
from ..scrapers.errors import SourceError
from ..config import MAX_CONCURRENT_SITES, SOURCE_SEARCH_TIMEOUT_SECONDS, source_config

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
            source.last_status=status;source.last_message=None;source.last_checked_at=datetime.now(timezone.utc).isoformat()
            return {"source": key, "label": source.label, "status": status, "search_method": source.last_method, "diagnostics":_diagnostics(source), "results": results[:limit], "error": None}
        except asyncio.TimeoutError:
            source.last_status="timeout";source.last_message=f"Search exceeded {SOURCE_SEARCH_TIMEOUT_SECONDS:g} seconds.";source.last_checked_at=datetime.now(timezone.utc).isoformat()
            return {"source":key,"label":source.label,"status":"timeout","search_method":source.last_method or source.search_method,"diagnostics":_diagnostics(source),
                "results":[],"error":{"code":"TIMEOUT","message":source.last_message}}
        except SourceError as exc:
            source.last_status=exc.status;source.last_message=exc.message;source.last_method=exc.method or source.last_method;source.last_checked_at=datetime.now(timezone.utc).isoformat()
            log.warning("%s search %s (%s): %s", key, exc.status, exc.code, exc.message)
            return {"source":key,"label":source.label,"status":exc.status,"search_method":source.last_method,"diagnostics":_diagnostics(source),
                "results":[],"error":{"code":exc.code,"message":exc.message}}
        except Exception as exc:
            log.warning("%s search failed: %s", key, exc)
            source.last_status="network_error";source.last_message=f"{type(exc).__name__}";source.last_checked_at=datetime.now(timezone.utc).isoformat()
            return {"source":key,"label":source.label,"status":"network_error","search_method":source.last_method,"diagnostics":_diagnostics(source),
                "results":[],"error":{"code":"UNEXPECTED_ERROR","message":"Unable to search this source right now."}}
    return await asyncio.gather(*(one(k, s) for k, s in SOURCES.items()))

async def search_source(query: str, key: str, limit: int = 10):
    if key not in SOURCES: raise KeyError(key)
    source=SOURCES[key]
    try:
        results=await asyncio.wait_for(source.search(query,limit),timeout=SOURCE_SEARCH_TIMEOUT_SECONDS)
        status="success_empty" if not results else "success"
        source.last_status=status;source.last_message=None;source.last_checked_at=datetime.now(timezone.utc).isoformat()
        return {"source":key,"label":source.label,"status":status,"search_method":source.last_method,"diagnostics":_diagnostics(source),"results":results,"error":None}
    except asyncio.TimeoutError:
        source.last_status="timeout";source.last_message=f"Search exceeded {SOURCE_SEARCH_TIMEOUT_SECONDS:g} seconds.";source.last_checked_at=datetime.now(timezone.utc).isoformat()
        return {"source":key,"label":source.label,"status":"timeout","search_method":source.last_method or source.search_method,"diagnostics":_diagnostics(source),"results":[],"error":{"code":"TIMEOUT","message":source.last_message}}
    except SourceError as exc:
        source.last_status=exc.status;source.last_message=exc.message;source.last_method=exc.method or source.last_method;source.last_checked_at=datetime.now(timezone.utc).isoformat()
        return {"source":key,"label":source.label,"status":exc.status,"search_method":source.last_method,"diagnostics":_diagnostics(source),"results":[],
                "error":{"code":exc.code,"message":exc.message}}

def source_diagnostics():
    output=[]
    for key,source in SOURCES.items():
        status,message=source.last_status,source.last_message
        if key=="thingiverse":
            from ..config import THINGIVERSE_API_KEY
            if not THINGIVERSE_API_KEY and status in {"ready","authentication_required"}:status,message="api_key_required","Set THINGIVERSE_API_KEY to use the documented Thingiverse API."
        config=source_config(key)
        if not config["enabled"] or config["access_mode"]=="disabled":status,message="unsupported",f"{source.label} is disabled."
        item={"id":key,"name":source.label,"enabled":config["enabled"] and config["access_mode"]!="disabled","access_mode":config["access_mode"],"search_method":source.last_method or source.search_method,
            "status":status,"message":message,"last_check":getattr(source,"last_checked_at",None),"diagnostics":_diagnostics(source)}
        if key=="thingiverse":
            from ..config import THINGIVERSE_API_KEY
            item["api_key"]="configured" if THINGIVERSE_API_KEY else "not_configured"
            if not item["enabled"]:item["api"]=item["search"]="disabled"
            else:
                item["api"]="reachable" if source.last_status in {"success","success_empty"} else "unavailable" if source.last_status not in {"ready","authentication_required","api_key_required"} else "not_checked"
                item["search"]="available" if source.last_status in {"success","success_empty"} else "unavailable" if source.last_status not in {"ready","authentication_required","api_key_required"} else "api_key_required" if not THINGIVERSE_API_KEY else "not_checked"
        output.append(item)
    return output
