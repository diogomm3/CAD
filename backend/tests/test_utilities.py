import zipfile
from pathlib import Path
from app.utils.filenames import safe_name, category_for
from app.services.download_service import _extract_zip, _safe_project

def test_query_and_filename_sanitizing():
    assert safe_name("Thor Hammer") == "Thor_Hammer"
    assert safe_name("../secret.txt") == "secret.txt"
    assert safe_name("Thor/Hammer") == "Thor_Hammer"

def test_extension_classification():
    assert category_for("model.stl") == "STL"
    assert category_for("design.FCStd") == "SOURCE"
    assert category_for("model.step") == "CAD"
    assert category_for("readme.txt") == "OTHER"

def test_zip_extract_skips_path_traversal(tmp_path):
    archive = tmp_path / "models.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../outside.stl", "unsafe")
        zf.writestr("parts/hammer.stl", "safe")
    root = tmp_path / "v1"
    root.mkdir()
    records = []
    _extract_zip(archive, root, records)
    assert not (tmp_path / "outside.stl").exists()
    assert (root / "STL" / "parts" / "hammer.stl").exists()
    assert records[0]["category"] == "STL"
    assert len(records[0]["sha256"]) == 64

def test_project_paths_are_siblings_and_versions_do_not_overwrite(tmp_path, monkeypatch):
    from app.services import download_service
    monkeypatch.setattr(download_service, "PROJECTS_DIR", tmp_path)
    first = _safe_project("Thor/Hammer")
    first.mkdir()
    second = _safe_project("Thor/Hammer")
    assert first.parent == tmp_path
    assert second.name == "Thor_Hammer_2"
    assert second.parent == tmp_path
