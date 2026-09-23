import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import CORS_ORIGINS
from .routes import search, projects

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
@asynccontextmanager
async def lifespan(_app):
    yield
    from .browser import BROWSER
    await BROWSER.close()

app=FastAPI(title="3D Model Finder", version="0.1.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False, allow_methods=["GET","POST"], allow_headers=["*"])
app.include_router(search.router, prefix="/api")
app.include_router(projects.router, prefix="/api")

@app.get("/api/health")
async def health(): return {"status":"ok"}
