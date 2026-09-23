import hashlib, json, logging, shutil, tempfile, zipfile, os, mimetypes, struct
import ipaddress, socket
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
import httpx
from ..config import PROJECTS_DIR, HTTP_TIMEOUT_SECONDS, MAX_FILE_SIZE_MB
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
            if "text/html" in ctype: raise ValueError("unexpected_content_type: Download URL returned HTML")
            if resp.headers.get("content-length") and int(resp.headers["content-length"])>MAX_FILE_SIZE_MB*1024*1024:raise ValueError("file_too_large")
            total=0
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    total+=len(chunk)
                    if total>MAX_FILE_SIZE_MB*1024*1024:dest.unlink(missing_ok=True);raise ValueError("file_too_large")
                    f.write(chunk)
            return ctype

def _record_file(src: Path, root: Path, category: str, records: list, original: str | None = None, relative_parent: str = "", target_name: str | None = None):
    bucket = BASE_DIRS.get(category, "OTHER")
    nested=Path(*(safe_name(part) for part in Path(relative_parent).parts if part not in {".",".."})) if relative_parent else Path()
    folder = root / bucket / nested; folder.mkdir(parents=True, exist_ok=True)
    digest_obj=hashlib.sha256();size=0
    with src.open("rb") as inp:
        for chunk in iter(lambda:inp.read(1024*1024),b""):
            size+=len(chunk);digest_obj.update(chunk)
    digest=digest_obj.hexdigest()
    if any(item.get("sha256") == digest for item in records): return
    dest = folder / safe_name(target_name or src.name)
    if dest.exists(): dest = folder / f"{src.stem}_{digest[:8]}{src.suffix}"
    staged=dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    shutil.copy2(src, staged)
    os.replace(staged,dest)
    records.append({"original_name": original or src.name, "local_path": str(dest.relative_to(root)), "category": bucket,
      "extension": dest.suffix.lower(), "size_bytes": size, "sha256": digest, "mime_type": mimetypes.guess_type(dest.name)[0],"source_url":None})

def _validate_payload(path: Path, extension: str):
    size=path.stat().st_size
    if size<=0:raise ValueError("empty_download")
    if size>MAX_FILE_SIZE_MB*1024*1024:raise ValueError("file_too_large")
    with path.open("rb") as inp:head=inp.read(4096)
    if head.lstrip().lower().startswith((b"<!doctype html",b"<html",b"<head",b"<body")):
        raise ValueError("unexpected_content_type: HTML error page received")
    ext=extension.lower()
    if ext==".stl":
        binary=False
        if size>=84:
            inp=path.open("rb");inp.seek(80);count=struct.unpack("<I",inp.read(4))[0];inp.close()
            binary=84+50*count<=size and count>0
        ascii_stl=head.lstrip().lower().startswith(b"solid") and b"facet" in head.lower()
        if not(binary or ascii_stl):raise ValueError("invalid_file_signature: payload is not a plausible STL")
    elif ext==".3mf":
        try:
            with zipfile.ZipFile(path) as archive:
                if sum(info.file_size for info in archive.infolist())>MAX_FILE_SIZE_MB*1024*1024:raise ValueError("file_too_large")
                if archive.testzip() or not any(n.lower()=="3d/3dmodel.model" for n in archive.namelist()):raise ValueError("invalid_file_signature: payload is not a valid 3MF")
        except zipfile.BadZipFile as exc:raise ValueError("invalid_file_signature: payload is not a valid 3MF") from exc
    elif ext==".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                if sum(info.file_size for info in archive.infolist())>MAX_FILE_SIZE_MB*1024*1024:raise ValueError("file_too_large: expanded archive exceeds configured limit")
        except zipfile.BadZipFile as exc:raise ValueError("invalid_file_signature: payload is not a ZIP archive") from exc

def _extract_zip(path: Path, root: Path, records: list):
    expanded=0;limit=MAX_FILE_SIZE_MB*1024*1024
    with zipfile.ZipFile(path) as zf:
        for member in zf.infolist():
            p = PurePosixPath(member.filename)
            if p.is_absolute() or ".." in p.parts or member.is_dir(): continue
            expanded+=member.file_size
            if expanded>limit:raise ValueError("file_too_large: expanded archive exceeds configured limit")
            ext = Path(p.name).suffix.lower()
            if ext in {".zip", ".rar", ".7z", ".gcode", ".bgcode"}: continue
            with tempfile.TemporaryDirectory(dir=root.parent) as td:
                temp = Path(td) / safe_name(p.name)
                with zf.open(member) as inp, temp.open("wb") as out: shutil.copyfileobj(inp, out)
                _validate_payload(temp,ext)
                _record_file(temp, root, category_for(p.name), records, p.name, str(Path(*p.parts[:-1])))

async def download_model(model: ModelResult, root: Path, query: str, version: str, status_callback=None):
    for folder in ("Image", "STL", "3MF", "CAD", "Source"): (root / folder).mkdir(parents=True, exist_ok=True)
    records, errors, file_statuses = [], [], []
    if status_callback:status_callback("searching",None)
    try:
        source=SOURCES[model.source]
        details = await source.get_model_details(model.model_url)
        files = await source.get_downloads(details)
        model = model.model_copy(update={"title": details.title or model.title, "author": details.author or model.author,
            "thumbnail_url": details.thumbnail_url or model.thumbnail_url,
            "available_files": files or details.available_files or model.available_files,
            "description": details.description or model.description, "license": details.license or model.license})
        if status_callback:status_callback("downloading",None)
    except Exception as exc:
        log.info("Could not retrieve detail files for %s: %s", model.source, exc)
        errors.append({"file":"model details", "reason":str(exc)[:180]})
        if status_callback:status_callback("downloading",None)
    for image_url in ([model.thumbnail_url] if model.thumbnail_url else []):
        temp=None
        try:
            SOURCES[model.source].validate_download_url(image_url)
            ext = Path(urlparse(image_url).path).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}: ext = ".jpg"
            temp = root.parent / f"preview-{next(tempfile._get_candidate_names())}{ext}"
            image_mime=await _download(image_url, temp)
            # Only accept actual image responses.
            with temp.open("rb") as image_file:signature=image_file.read(12)
            if signature.startswith(b"\xff\xd8\xff"):ext=".jpg"
            elif signature.startswith(b"\x89PNG"):ext=".png"
            elif signature.startswith(b"GIF8"):ext=".gif"
            elif signature.startswith(b"RIFF") and signature[8:12]==b"WEBP":ext=".webp"
            else:raise ValueError("unexpected_content_type: Thumbnail was not an image")
            dest = root / "Image" / f"preview{ext}"; shutil.move(str(temp), dest)
            digest=hashlib.sha256()
            with dest.open("rb") as image_file:
                for chunk in iter(lambda:image_file.read(1024*1024),b""):digest.update(chunk)
            records.append({"original_name":dest.name,"local_path":f"Image/{dest.name}","category":"Image","extension":ext,"size_bytes":dest.stat().st_size,"sha256":digest.hexdigest(),"mime_type":image_mime or mimetypes.guess_type(dest.name)[0],"source_url":image_url})
        except Exception as exc: errors.append({"file":"preview image", "reason":str(exc)[:180]})
        finally:
            if temp:temp.unlink(missing_ok=True)
    for file in model.available_files:
        if not file.url or not file.downloadable:
            skipped={"file":file.name,"status":"skipped","reason":file.reason or "not_available"};errors.append({"file":file.name,"reason":file.reason or "not_available"});file_statuses.append(skipped)
            if status_callback:status_callback("downloading",skipped.copy())
            continue
        temp = root.parent / f"asset-{next(tempfile._get_candidate_names())}-{safe_name(file.name)}"
        file_status={"file":file.name,"status":"queued"};file_statuses.append(file_status)
        if status_callback:status_callback("downloading",file_status.copy())
        file_status["status"]="downloading"
        if status_callback:status_callback("downloading",file_status.copy())
        try:
            source = SOURCES[model.source]
            source.validate_download_url(file.url)
            await source.download_file(file, temp)
            _validate_payload(temp,file.extension or temp.suffix)
            if temp.suffix.lower() == ".zip":
                before=len(records)
                _extract_zip(temp, root, records)
                if len(records)==before:_record_file(temp,root,"OTHER",records,file.name,target_name=file.name)
                for record in records:
                    if record.get("source_url") is None:record["source_url"]=file.url
            else:
                before=len(records)
                _record_file(temp, root, file.category or category_for(temp.name), records, file.name,target_name=file.name)
                if len(records)>before:
                    record=records[-1];record["source_url"]=file.url
                    record["mime_type"]=file.mime_type or mimetypes.guess_type(file.name)[0]
                    file.local_path=record["local_path"];file.sha256=record["sha256"];file.size_bytes=record["size_bytes"];file.original_name=file.name;file.source_url=file.url
            file_status["status"]="completed"
            if status_callback:status_callback("downloading",file_status.copy())
        except Exception as exc:
            message=str(exc)
            reason="file_too_large" if "file_too_large" in message else "unexpected_content_type" if "unexpected_content_type" in message else "invalid_file_signature" if "invalid_file_signature" in message else "download_failed"
            errors.append({"file":file.name,"reason":reason,"message":message[:180]})
            file_status.update(status="failed",reason=reason)
            if status_callback:status_callback("downloading",file_status.copy())
        finally: temp.unlink(missing_ok=True)
    meta = {"project_query":query,"version":version,"source":{"website":SOURCES[model.source].label,"model_id":model.id,"model_url":model.model_url,"title":model.title,"author":model.author,"license":model.license,"published_at":model.published_at},
      "statistics":{"downloads":model.downloads,"likes":model.likes,"favorites":model.favorites,"rating":model.rating,"popularity_label":model.popularity_label},
      "description":model.description,"downloaded_at":datetime.now(timezone.utc).isoformat(),"files":records,"file_statuses":file_statuses,"download_errors":errors,
      "status":"complete" if records and not errors else "partial" if records else "failed"}
    (root / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta
