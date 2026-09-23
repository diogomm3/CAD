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

Copy `backend/.env.example` to `backend/.env`. A relative `PROJECTS_DIR` is resolved from the backend directory, so the default `../Projects` points to the repository's sibling `Projects/` directory.

Playwright setup has three parts:

1. **Python dependencies:** `cd backend && .venv/bin/pip install -r requirements.txt`.
2. **Browser binaries:** `.venv/bin/playwright install chromium`.
3. **OS libraries:** on Linux, `.venv/bin/playwright install-deps chromium` (requires system package installation privileges). Docker installs Chromium and the required libraries in its image.

Check the browser with `.venv/bin/python -m app.tools.test_browser`. This opens `example.com` and verifies navigation, JavaScript evaluation, title, and DOM text. Other settings include `HTTP_TIMEOUT_SECONDS`, `SOURCE_SEARCH_TIMEOUT_SECONDS`, `REQUEST_DELAY_MS`, `MAX_CONCURRENT_SITES`, `MAX_CONCURRENT_DOWNLOADS`, `LOG_LEVEL`, `CORS_ORIGINS`, `PLAYWRIGHT_ENABLED`, `BROWSER_PROFILE_DIR`, `BROWSER_AUTH_ENABLED`, and `DEBUG_SCRAPERS`.

Thingiverse's [official developer API](https://www.thingiverse.com/developers/swagger) requires an application token. Set `THINGIVERSE_API_KEY` in `backend/.env`; without it, the adapter reports `authentication_required` rather than an empty result. Do not put credentials in source code.

### Optional authenticated browser profile

Public browser rendering uses a clean shared browser context. To test normal user-authenticated access, log in manually to the persistent profile at `BROWSER_PROFILE_DIR` (default `../browser-profile`). Set `BROWSER_HEADLESS=false`, then from `backend/` run:

```bash
.venv/bin/python -m app.tools.login_source grabcad
```

Log into the site yourself in the opened browser, then stop the command with Ctrl+C. Set `BROWSER_AUTH_ENABLED=true` to allow an authenticated browser attempt after public access fails. The profile stores browser session data, not usernames or passwords. Keep `browser-profile/` private; it is excluded from Git.

### Adapter diagnostics and live checks

`GET /api/sources/status` reports each adapter's last observed state and access method. Search states distinguish `success_empty`, `blocked`, `authentication_required`, `rate_limited`, `parse_error`, `network_error`, and `timeout`. It includes per-stage HTTP/public-browser/authenticated-browser diagnostics. Retry one source with `POST /api/search/source` and `{ "query": "thor hammer", "source": "grabcad" }`.

Fixture-based parsing tests run with `cd backend && .venv/bin/pytest -q`; they do not contact model sites. After changing adapters, run the live read-only smoke check with `cd backend && .venv/bin/python ../scripts/smoke_test_sources.py "thor hammer"`. For one source use `.venv/bin/python -m app.tools.test_source printables "thor hammer"`. To inspect a model use `.venv/bin/python -m app.tools.test_model printables MODEL_ID`. The separate `app.tools.download_model` command downloads files and should only be used for a model whose license and download permission allow it.

Run `.venv/bin/python -m app.tools.browser_diagnostic "https://example.com"` to save sanitized HTML, screenshot, console errors, and response URLs under `backend/debug/browser/`. Add `--authenticated` to use the manual-login profile. Run `.venv/bin/python -m app.tools.inspect_page "https://grabcad.com/library?query=thor%20hammer"` to summarize links, images, buttons, inputs, and likely model links. Set `DEBUG_SCRAPERS=true` to save sanitized fetched HTML/JSON and browser screenshots under `backend/debug/<source>/` when parsing fails. Debug artifacts and the browser profile are ignored by Git. Diagnostic files do not include cookies or request authorization headers; inspect screenshots and page content before sharing them.

### Current live adapter check

The browser smoke test passed: Chromium launched, navigated to `example.com`, and read its title and DOM. The current public GrabCAD browser diagnostic returned HTTP 403 from CloudFront with no links or result markup. Current HTTP-only search returns HTTP 200 but only a JavaScript-required shell and no library result links. Printables and MakerWorld return HTTP 403 to HTTP access. Thingiverse requires an API key. Browser operation and site access are separate checks; a passing browser test does not imply that a site allows automated access.

| Source | Search | Details | Files | Live verified |
|---|---|---|---|---|
| Printables | blocked (403) | not checked | not checked | no |
| MakerWorld | blocked (403) | not checked | not checked | no |
| Thingiverse | API key required | not checked | not checked | no |
| GrabCAD | HTTP 200 JavaScript shell; browser 403 | not checked | not checked | no |

## Supported websites and scraping

Printables, MakerWorld, and GrabCAD try a normal public HTML request, then a shared Playwright browser for public rendered pages. Thingiverse uses its official documented API. One source failure does not interrupt others. Sites may change their markup or block automated access; those conditions appear as a typed source status and error, separate from a successful empty search. Detail page parsing discovers linked files by extension. Downloads are accepted only over HTTPS on adapter-approved source/CDN domains. Authentication, CAPTCHA, access controls, and anti-bot challenges are not bypassed. Results and downloads must follow the site's terms, license, and rate limits.

Search returns up to 10 per source in the order supplied by that source's search/API. Counts are not fabricated. Metrics that aren't reliably parsed are null. Download discovery is intentionally conservative: an unrecognized CDN is reported as unavailable until that host is explicitly validated in the source adapter.

## Download organization and file categories

Every selected model gets a version folder in selection order. Categories are based on extension: STL, 3MF, CAD (STEP/STP/IGES/IGS and common SolidWorks parts/assemblies), Source (F3D/SCAD/Blend/FCStd), or OTHER. ZIP files are checked for traversal paths and recognized members are categorized. Empty category folders are expected. Each `metadata.json` retains source, URL, author if found, license if found, timestamps, file size, SHA-256, and download errors.

Example: searching `Thor Hammer` creates `Projects/Thor_Hammer/v1/` and `v2/`. If that project folder already exists, a numbered sibling such as `Thor_Hammer_2` is used; existing data is not overwritten.

## Limitations

This is a local MVP. Public site HTML is not a stable API; result titles, authors, popularity statistics, licenses, and files may be missing. Some model pages need a normal logged-in user session or site-specific download actions. GrabCAD and other sources may block automated access. Downloads can therefore be partial. Job progress is held in memory and resets if the backend restarts. The API response shows current per-source state, and the UI offers a retry for each failed source.

## Troubleshooting

- Check backend logs and `GET /api/health` if the UI cannot search.
- Check `CORS_ORIGINS` if using a frontend URL other than `localhost:5173`.
- A source error/empty result can reflect changed markup, JavaScript rendering, rate limiting, or site access controls.
- Inspect `metadata.json` for per-file failures and attribution.
- Keep `Projects/` out of version control; `.gitignore` excludes it.

## Add a source adapter

Implement `ModelSource` in `backend/app/scrapers/`, expose normalized `ModelResult` objects, constrain accepted model and download hosts, then register the adapter in `scrapers/registry.py`. The API and frontend use the normalized source key and need no source-specific changes.
