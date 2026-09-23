import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import CORS_ORIGINS, PROJECTS_DIR
from .routes import search, projects

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app=FastAPI(title="3D Model Finder", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False, allow_methods=["GET","POST"], allow_headers=["*"])
app.include_router(search.router, prefix="/api")
app.include_router(projects.router, prefix="/api")

@app.get("/api/health")
async def health(): return {"status":"ok","projects_dir":str(PROJECTS_DIR)}
