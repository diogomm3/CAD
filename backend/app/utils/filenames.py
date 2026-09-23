import re
from pathlib import Path
from urllib.parse import urlparse

EXT_CATEGORIES = {
    ".stl": "STL", ".3mf": "3MF", ".step": "CAD", ".stp": "CAD",
    ".iges": "CAD", ".igs": "CAD", ".step.gz": "CAD",
    ".f3d": "SOURCE", ".scad": "SOURCE", ".blend": "SOURCE", ".fcstd": "SOURCE",
    ".sldprt": "SOURCE", ".sldasm": "SOURCE", ".slddrw": "SOURCE",
    ".ipt": "SOURCE", ".iam": "SOURCE", ".prt": "SOURCE", ".asm": "SOURCE",
    ".catpart": "SOURCE", ".catproduct": "SOURCE", ".3dxml": "SOURCE",
    ".obj": "OTHER", ".dxf": "OTHER", ".zip": "OTHER", ".rar": "OTHER", ".7z": "OTHER",
}

def safe_name(value: str, fallback: str = "item") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return (value[:100] or fallback)

def category_for(name: str) -> str:
    return EXT_CATEGORIES.get(Path(name).suffix.lower(), "OTHER")

def filename_from_url(url: str, fallback: str = "download") -> str:
    return safe_name(Path(urlparse(url).path).name or fallback, fallback)
