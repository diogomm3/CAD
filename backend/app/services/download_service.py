import hashlib, json, logging, shutil, tempfile, zipfile
import ipaddress, socket
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
import httpx
from ..config import PROJECTS_DIR, HTTP_TIMEOUT_SECONDS
from ..models import ModelResult
from ..scrapers.registry import SOURCES
from ..utils.filenames import safe_name, category_for, filename_from_url

log = logging.getLogger(__name__)
BASE_DIRS = {"STL":"STL", "3MF":"3MF", "CAD":"CAD", "SOURCE":"Source", "IMAGE":"Image", "OTHER":"OTHER"}

def _safe_project(query: str) -> Path:
    name = safe_name(query.replace(" ", "_"), "Project")
    target = (PROJECTS_DIR / name).resolve()
    if not target.is_relative_to(PROJECTS_DIR.resolve()): raise ValueError("Invalid project name")
    i = 2
    while target.exists():
        target = (PROJECTS_DIR / f"{name}_{i}").resolve(); i += 1
    return target

async def _download(url: str, dest: Path):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() == "localhost":
        raise ValueError("Unsafe download URL")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, None)}
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("Unsafe download host")
    except socket.gaierror as exc:
        raise ValueError("Download host could not be resolved") from exc
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False,
      headers={"User-Agent":"Mozilla/5.0 (compatible; LocalModelFinder/1.0)"}) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "text/html" in ctype: raise ValueError("Download URL returned HTML")
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(): f.write(chunk)
            return ctype

def _record_file(src: Path, root: Path, category: str, records: list, original: str | None = None, relative_parent: str = ""):
    bucket = BASE_DIRS.get(category, "OTHER")
    nested=Path(*(safe_name(part) for part in Path(relative_parent).parts if part not in {".",".."})) if relative_parent else Path()
    folder = root / bucket / nested; folder.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if any(item.get("sha256") == digest for item in records): return
    dest = folder / safe_name(src.name)
    if dest.exists(): dest = folder / f"{src.stem}_{digest[:8]}{src.suffix}"
    shutil.copy2(src, dest)
    data = dest.read_bytes()
    records.append({"original_name": original or src.name, "local_path": str(dest.relative_to(root)), "category": bucket,
      "extension": dest.suffix.lower(), "size_bytes": len(data), "sha256": digest, "mime_type": None})

def _extract_zip(path: Path, root: Path, records: list):
    with zipfile.ZipFile(path) as zf:
        for member in zf.infolist():
            p = PurePosixPath(member.filename)
            if p.is_absolute() or ".." in p.parts or member.is_dir(): continue
            ext = Path(p.name).suffix.lower()
            if ext in {".zip", ".rar", ".7z", ".gcode", ".bgcode"}: continue
            with tempfile.TemporaryDirectory(dir=root.parent) as td:
                temp = Path(td) / safe_name(p.name)
                with zf.open(member) as inp, temp.open("wb") as out: shutil.copyfileobj(inp, out)
                _record_file(temp, root, category_for(p.name), records, p.name, str(Path(*p.parts[:-1])))

async def download_model(model: ModelResult, root: Path, query: str, version: str):
    for folder in ("Image", "STL", "3MF", "CAD", "Source"): (root / folder).mkdir(parents=True, exist_ok=True)
    records, errors = [], []
    try:
        source=SOURCES[model.source]
        details = await source.get_model_details(model.model_url)
        files = await source.get_downloads(details)
        model = model.model_copy(update={"title": details.title or model.title, "author": details.author or model.author,
            "thumbnail_url": details.thumbnail_url or model.thumbnail_url,
            "available_files": files or details.available_files or model.available_files,
            "description": details.description or model.description, "license": details.license or model.license})
    except Exception as exc:
        log.info("Could not retrieve detail files for %s: %s", model.source, exc)
        errors.append({"file":"model details", "reason":str(exc)[:180]})
    for image_url in ([model.thumbnail_url] if model.thumbnail_url else []):
        try:
            SOURCES[model.source].validate_download_url(image_url)
            ext = Path(urlparse(image_url).path).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}: ext = ".jpg"
            temp = root.parent / f"preview-{next(tempfile._get_candidate_names())}{ext}"
            await _download(image_url, temp)
            # Only accept actual image responses.
            if temp.read_bytes()[:3] not in (b"\xff\xd8\xff",) and not temp.read_bytes().startswith((b"\x89PNG", b"RIFF", b"GIF8")):
                temp.unlink(missing_ok=True); raise ValueError("Thumbnail was not an image")
            dest = root / "Image" / f"preview{ext}"; shutil.move(str(temp), dest)
            data=dest.read_bytes(); records.append({"original_name":dest.name,"local_path":f"Image/{dest.name}","category":"Image","size_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
        except Exception as exc: errors.append({"file":"preview image", "reason":str(exc)[:180]})
    for file in model.available_files:
        if not file.url or not file.downloadable:
            errors.append({"file":file.name,"reason":file.reason or "not_available"}); continue
        temp = root.parent / f"asset-{next(tempfile._get_candidate_names())}-{filename_from_url(file.url)}"
        try:
            source = SOURCES[model.source]
            source.validate_download_url(file.url)
            await source.download_file(file, temp)
            if temp.suffix.lower() == ".zip": _extract_zip(temp, root, records)
            else: _record_file(temp, root, file.category or category_for(temp.name), records, file.name)
        except Exception as exc: errors.append({"file":file.name,"reason":str(exc)[:180]})
        finally: temp.unlink(missing_ok=True)
    meta = {"project_query":query,"version":version,"source":{"website":SOURCES[model.source].label,"model_id":model.id,"model_url":model.model_url,"title":model.title,"author":model.author,"license":model.license,"published_at":model.published_at},
      "statistics":{"downloads":model.downloads,"likes":model.likes,"favorites":model.favorites,"rating":model.rating,"popularity_label":model.popularity_label},
      "description":model.description,"downloaded_at":datetime.now(timezone.utc).isoformat(),"files":records,"download_errors":errors,
      "status":"complete" if records and not errors else "partial" if records else "failed"}
    (root / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta
