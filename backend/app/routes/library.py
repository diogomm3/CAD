import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..config import PROJECTS_DIR
from ..utils.filenames import category_for

router = APIRouter()
VERSION_PATTERN = __import__("re").compile(r"^v(\d+)$", __import__("re").IGNORECASE)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _safe_project_path(project_id: str) -> Path:
    root = PROJECTS_DIR.resolve()
    target = (root / project_id).resolve()
    if not target.is_relative_to(root) or not target.is_dir():
        raise HTTPException(404, "Project not found")
    return target


def _file_category(path: Path) -> str:
    parent = path.parent.name.upper()
    if parent == "IMAGE":
        return "Image"
    if parent in {"STL", "3MF", "CAD", "SOURCE", "OTHER"}:
        return parent
    return category_for(path.name)


def _files(root: Path, public_prefix: str) -> list[dict]:
    output = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in {"project.json", "metadata.json"} or path.name.endswith(":Zone.Identifier"):
            continue
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        output.append({
            "name": path.name,
            "path": relative,
            "category": _file_category(path),
            "extension": path.suffix.lower(),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "url": f"{public_prefix}/files/{relative}",
        })
    return output


def _version_dirs(project: Path) -> list[tuple[str, Path]]:
    versions = []
    for child in project.iterdir():
        if child.is_dir() and VERSION_PATTERN.match(child.name):
            versions.append((child.name, child))
    return sorted(versions, key=lambda item: int(VERSION_PATTERN.match(item[0]).group(1)))


def _version(project: Path, name: str, manifest_version: dict | None = None) -> dict:
    metadata = _read_json(project / "metadata.json")
    if name != "v1" or not metadata:
        metadata = _read_json(project / name / "metadata.json")
    version_root = project if name == "v1" and not (project / name).is_dir() else project / name
    source = metadata.get("source") or {}
    files = _files(version_root, f"/api/library/{project.name}/{name}")
    recorded_files = metadata.get("files") or []
    return {
        "version": name,
        "title": source.get("title") or (manifest_version or {}).get("title") or project.name,
        "author": source.get("author") or (manifest_version or {}).get("author"),
        "source": source.get("website") or (manifest_version or {}).get("source"),
        "model_id": source.get("model_id") or (manifest_version or {}).get("model_id"),
        "model_url": source.get("model_url") or (manifest_version or {}).get("model_url"),
        "license": source.get("license"),
        "description": metadata.get("description"),
        "status": metadata.get("status") or (manifest_version or {}).get("status") or "complete",
        "downloaded_at": metadata.get("downloaded_at"),
        "files": files,
        "file_count": len(files) or len(recorded_files),
    }


def _project(project: Path) -> dict:
    manifest = _read_json(project / "project.json")
    manifest_versions = {item.get("version"): item for item in manifest.get("versions", []) if isinstance(item, dict)}
    directories = _version_dirs(project)
    if directories:
        versions = [_version(project, name, manifest_versions.get(name)) for name, _ in directories]
    else:
        versions = [_version(project, "v1", manifest_versions.get("v1"))]
    stat = project.stat()
    updated = max((Path(file).stat().st_mtime for file in project.rglob("*") if Path(file).is_file()), default=stat.st_mtime)
    created = manifest.get("created_at") or datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat()
    return {
        "id": project.name,
        "name": project.name,
        "query": manifest.get("query") or project.name.replace("_", " "),
        "created_at": created,
        "updated_at": datetime.fromtimestamp(updated, timezone.utc).isoformat(),
        "version_count": len(versions),
        "file_count": sum(version["file_count"] for version in versions),
        "versions": versions,
    }


@router.get("/library")
async def list_library(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query("", max_length=120),
    sort: str = Query("updated", pattern="^(updated|name|files)$"),
):
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = [_project(path) for path in PROJECTS_DIR.iterdir() if path.is_dir() and not path.name.startswith(".")]
    needle = q.strip().lower()
    if needle:
        projects = [project for project in projects if needle in project["name"].lower() or needle in project["query"].lower()]
    if sort == "name":
        projects.sort(key=lambda project: project["name"].lower())
    elif sort == "files":
        projects.sort(key=lambda project: (project["file_count"], project["updated_at"]), reverse=True)
    else:
        projects.sort(key=lambda project: project["updated_at"], reverse=True)
    total = len(projects)
    start = (page - 1) * page_size
    return {"page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size, "projects": projects[start:start + page_size]}


@router.get("/library/{project_id}")
async def library_project(project_id: str):
    return _project(_safe_project_path(project_id))


@router.get("/library/{project_id}/{version}/files/{file_path:path}")
async def library_file(project_id: str, version: str, file_path: str):
    project = _safe_project_path(project_id)
    root = project / version if (project / version).is_dir() else project
    target = (root / file_path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file() or target.name.endswith(":Zone.Identifier"):
        raise HTTPException(404, "File not found")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")
