import asyncio, uuid, logging, json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..schemas import ProjectRequest
from ..config import PROJECTS_DIR
from ..models import ModelResult
from ..scrapers.registry import SOURCES
from ..services.download_service import _safe_project, download_model

router = APIRouter()
log = logging.getLogger(__name__)
JOBS: dict[str, dict] = {}

async def run_job(job_id, request):
    job=JOBS[job_id]; job.update(status="searching", message="Preparing project folders")
    try:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        project = _safe_project(request.query); project.mkdir(parents=True, exist_ok=True)
        outcomes=[]; versions=[]; created_at=datetime.now(timezone.utc).isoformat()
        (project/"project.json").write_text(json.dumps({"query":request.query,"project_name":project.name,"created_at":created_at,"versions":versions},indent=2,ensure_ascii=False))
        for index, selected in enumerate(request.models, 1):
            source=SOURCES[selected.source]
            source.validate_url(selected.model_url)
            model=ModelResult(id=selected.model_id,source=selected.source,title=selected.model_id,model_url=selected.model_url)
            version=f"v{index}"; folder=project/version; folder.mkdir()
            job.update(progress=int((index-1)/len(request.models)*100),message=f"Downloading {version}",files={})
            def progress(stage,file_state):
                updates={"status":stage,"message":"Retrieving model details" if stage=="searching" else "Downloading model files"}
                if file_state:
                    states=dict(job.get("files",{}));states[file_state["file"]]=file_state;updates["files"]=states
                job.update(**updates)
            outcome=await download_model(model, folder, request.query, version,progress);outcomes.append(outcome)
            versions.append({"version":version,"source":source.label,"title":outcome["source"].get("title") or selected.model_id,"model_url":selected.model_url,"status":outcome["status"]})
            (project/"project.json").write_text(json.dumps({"query":request.query,"project_name":project.name,"created_at":created_at,"versions":versions},indent=2,ensure_ascii=False))
            job.update(progress=int(index/len(request.models)*100),projects=outcomes)
        complete=all(x["status"]=="complete" for x in outcomes)
        final_status="completed" if complete else "partial" if any(x["files"] for x in outcomes) else "failed"
        message="Downloads finished" if complete else "Downloads finished with some files unavailable" if final_status=="partial" else "Downloads failed"
        if final_status=="failed":
            reason=next((error.get("reason") or error.get("message") for outcome in outcomes for error in outcome.get("download_errors",[])),None)
            if reason:message=f"Downloads failed: {reason}"
        job.update(status=final_status,progress=100,message=message,projects=outcomes,
            project_name=project.name,version=f"v{len(request.models)}",path=f"{project.name}/v{len(request.models)}",project_path=project.name)
    except Exception as exc:
        log.exception("Project download job failed")
        job.update(status="failed",message=str(exc)[:200],progress=100)

@router.post("/projects", status_code=202)
async def create_project(request: ProjectRequest):
    if len(request.query.strip()) == 0 or len(request.query) > 120: raise HTTPException(422,"Invalid query")
    for model in request.models:
        if model.source not in SOURCES: raise HTTPException(422,f"Unknown source: {model.source}")
        if not SOURCES[model.source].config["enabled"] or SOURCES[model.source].config["access_mode"]=="disabled":raise HTTPException(422,f"Source is disabled: {model.source}")
        try: SOURCES[model.source].validate_url(model.model_url)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    job_id=uuid.uuid4().hex
    JOBS[job_id]={"job_id":job_id,"status":"queued","progress":0,"message":"Queued","projects":[]}
    asyncio.create_task(run_job(job_id, request))
    return JOBS[job_id]

class ImportRequest(BaseModel):
    model_url: str
    source: str | None = None
    query: str | None = None

@router.post("/models/import",status_code=202)
async def import_model(request: ImportRequest):
    from urllib.parse import urlparse
    host=(urlparse(request.model_url).hostname or "").lower()
    matches=[source for source in SOURCES.values() if source._host_allowed(host,source.domains)]
    if len(matches)!=1:raise HTTPException(422,"Model URL is not from a supported source")
    source=matches[0]
    if request.source and request.source!=source.key:raise HTTPException(422,"The selected source does not match the model URL")
    try:source.validate_url(request.model_url)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    if not source.config["enabled"] or source.config["access_mode"]=="disabled":raise HTTPException(422,"This source is disabled")
    model_id=request.model_url.rstrip("/").split("/")[-1]
    query=(request.query or model_id).strip()[:120] or model_id
    project_request=ProjectRequest(query=query,models=[{"source":source.key,"model_id":model_id,"model_url":request.model_url}])
    job_id=uuid.uuid4().hex
    JOBS[job_id]={"job_id":job_id,"status":"queued","progress":0,"message":"Queued URL import","projects":[]}
    asyncio.create_task(run_job(job_id,project_request))
    return JOBS[job_id]

@router.get("/projects/{job_id}")
async def get_job(job_id: str):
    if job_id not in JOBS: raise HTTPException(404,"Job not found")
    return JOBS[job_id]
