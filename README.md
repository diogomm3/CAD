# FormFinder

A local React and FastAPI app for searching public model listings on Printables, MakerWorld, Thingiverse, and GrabCAD Community, then collecting selected designs under `Projects/` with source attribution and per-file metadata.

## Architecture

`frontend/` is a React + TypeScript + Vite single page app. `backend/app/scrapers/` provides one adapter per community behind a shared async interface. Searches run concurrently and return a status for every source. `backend/app/services/download_service.py` creates a project folder with one `vN` for each selected model and records results in each version's `metadata.json`. There is no database; job status lives in memory and the project assets live on disk.

## Install and run locally

Backend (Python 3.12 recommended):

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Frontend, in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` to `http://localhost:8000`. Set `VITE_API_PROXY` when the backend is elsewhere. Build the frontend with `npm run build`.

Backend unit/API checks run with `cd backend && .venv/bin/pytest -q`.

## Docker

```bash
docker compose up --build
```

The UI is at `http://localhost:5173`; the API is at `http://localhost:8000`. `./Projects` is mounted at `/Projects` in the backend container.

## Configuration

Copy `backend/.env.example` to `backend/.env`. A relative `PROJECTS_DIR` is resolved from the backend directory, so the default `../Projects` points to the repository's sibling `Projects/` directory. `HTTP_TIMEOUT_SECONDS`, `REQUEST_DELAY_MS`, `MAX_CONCURRENT_SITES`, `MAX_CONCURRENT_DOWNLOADS`, `LOG_LEVEL`, and `CORS_ORIGINS` tune local operation. The initial HTML adapters don't use Playwright; browser support can be added within an individual adapter if ordinary public page HTML isn't enough.

## Supported websites and scraping

Each search adapter requests the site's public search page and parses model links and preview images from returned HTML. One source failure does not interrupt others. Sites may change their markup or block automated access, and JavaScript-rendered listings may not be present in returned HTML; those conditions show as empty results or a per-source error. The detail page parser discovers publicly linked model files by extension. Downloads are accepted only over HTTPS on that source's domain. Authentication, CAPTCHA, access controls, and anti-bot challenges are not bypassed. Results and downloads must follow the site's terms, license, and rate limits.

Search returns up to 10 per source in the order supplied by that source's search page. Counts are not fabricated. Metrics that aren't reliably parsed are null. Download discovery is intentionally conservative: a file served from a separate CDN is reported as unavailable until the site's adapter explicitly validates that host.

## Download organization and file categories

Every selected model gets a version folder in selection order. Categories are based on extension: STL, 3MF, CAD (STEP/STP/IGES/IGS and common SolidWorks parts/assemblies), Source (F3D/SCAD/Blend/FCStd), or OTHER. ZIP files are checked for traversal paths and recognized members are categorized. Empty category folders are expected. Each `metadata.json` retains source, URL, author if found, license if found, timestamps, file size, SHA-256, and download errors.

Example: searching `Thor Hammer` creates `Projects/Thor_Hammer/v1/` and `v2/`. If that project folder already exists, a numbered sibling such as `Thor_Hammer_2` is used; existing data is not overwritten.

## Limitations

This is a local MVP. Public site HTML is not a stable API; result titles, authors, popularity statistics, licenses, and files may be missing. Some model pages need JavaScript, login, or site-specific download actions. GrabCAD and other sources may block automated access. Downloads can therefore be partial. Job progress is held in memory and resets if the backend restarts. The UI currently reports running/complete state by polling and exposes the final metadata summary through project files.

## Troubleshooting

- Check backend logs and `GET /api/health` if the UI cannot search.
- Check `CORS_ORIGINS` if using a frontend URL other than `localhost:5173`.
- A source error/empty result can reflect changed markup, JavaScript rendering, rate limiting, or site access controls.
- Inspect `metadata.json` for per-file failures and attribution.
- Keep `Projects/` out of version control; `.gitignore` excludes it.

## Add a source adapter

Implement `ModelSource` in `backend/app/scrapers/`, expose normalized `ModelResult` objects, constrain accepted model and download hosts, then register the adapter in `scrapers/registry.py`. The API and frontend use the normalized source key and need no source-specific changes.
