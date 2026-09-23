import asyncio, uuid, logging
from fastapi import APIRouter, HTTPException
from ..schemas import ProjectRequest
from ..config import PROJECTS_DIR
from ..models import ModelResult
from ..scrapers.registry import SOURCES
from ..services.download_service import _safe_project, download_model

router = APIRouter()
log = logging.getLogger(__name__)
JOBS: dict[str, dict] = {}

async def run_job(job_id, request):
    job=JOBS[job_id]; job.update(status="running", message="Preparing project folders")
    try:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        project = _safe_project(request.query); project.mkdir(parents=True, exist_ok=True)
        outcomes=[]
        for index, selected in enumerate(request.models, 1):
            source=SOURCES[selected.source]
            source.validate_url(selected.model_url)
            model=ModelResult(id=selected.model_id,source=selected.source,title=selected.model_id,model_url=selected.model_url)
            version=f"v{index}"; folder=project/version; folder.mkdir()
            job.update(progress=int((index-1)/len(request.models)*100),message=f"Downloading {version}")
            outcomes.append(await download_model(model, folder, request.query, version))
        job.update(status="complete" if all(x["status"]=="complete" for x in outcomes) else "partial",progress=100,message="Downloads finished",projects=outcomes,project_path=str(project))
    except Exception as exc:
        log.exception("Project download job failed")
        job.update(status="failed",message=str(exc)[:200],progress=100)

@router.post("/projects", status_code=202)
async def create_project(request: ProjectRequest):
    if len(request.query.strip()) == 0 or len(request.query) > 120: raise HTTPException(422,"Invalid query")
    for model in request.models:
        if model.source not in SOURCES: raise HTTPException(422,f"Unknown source: {model.source}")
        try: SOURCES[model.source].validate_url(model.model_url)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    job_id=uuid.uuid4().hex
    JOBS[job_id]={"job_id":job_id,"status":"queued","progress":0,"message":"Queued","projects":[]}
    asyncio.create_task(run_job(job_id, request))
    return JOBS[job_id]

@router.get("/projects/{job_id}")
async def get_job(job_id: str):
    if job_id not in JOBS: raise HTTPException(404,"Job not found")
    return JOBS[job_id]
