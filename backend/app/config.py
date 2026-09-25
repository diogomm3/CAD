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
MAX_FILE_SIZE_MB = max(1, float(os.getenv("MAX_FILE_SIZE_MB", "1000")))

SOURCE_KEYS = ("printables", "makerworld")
SOURCE_CONFIG = {
    key: {
        "enabled": os.getenv(f"{key.upper()}_ENABLED", "true").lower() in {"1", "true", "yes"},
        "access_mode": os.getenv(f"{key.upper()}_ACCESS_MODE", "auto").lower(),
    }
    for key in SOURCE_KEYS
}
VALID_ACCESS_MODES = {"auto", "http", "browser", "authenticated_browser", "api", "disabled"}
for _source, _config in SOURCE_CONFIG.items():
    if _config["access_mode"] not in VALID_ACCESS_MODES:
        _config["access_mode"] = "disabled"

def source_config(key: str) -> dict:
    return SOURCE_CONFIG[key]
