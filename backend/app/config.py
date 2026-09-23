import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")
PROJECTS_DIR = (BACKEND_DIR / os.getenv("PROJECTS_DIR", "../Projects")).resolve()
HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
REQUEST_DELAY_MS = int(os.getenv("REQUEST_DELAY_MS", "500"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
MAX_CONCURRENT_SITES = int(os.getenv("MAX_CONCURRENT_SITES", "4"))
SOURCE_SEARCH_TIMEOUT_SECONDS = float(os.getenv("SOURCE_SEARCH_TIMEOUT_SECONDS", "30"))
CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if x.strip()]
PLAYWRIGHT_ENABLED = os.getenv("PLAYWRIGHT_ENABLED", "true").lower() in {"1", "true", "yes"}
DEBUG_SCRAPERS = os.getenv("DEBUG_SCRAPERS", "false").lower() in {"1", "true", "yes"}
DEBUG_DIR = Path(os.getenv("DEBUG_DIR", str(BACKEND_DIR / "debug"))).resolve()
BROWSER_PROFILE_DIR = (BACKEND_DIR / os.getenv("BROWSER_PROFILE_DIR", "../browser-profile")).resolve()
BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() in {"1", "true", "yes"}
BROWSER_AUTH_ENABLED = os.getenv("BROWSER_AUTH_ENABLED", "false").lower() in {"1", "true", "yes"}
THINGIVERSE_API_KEY = os.getenv("THINGIVERSE_API_KEY", "").strip()
