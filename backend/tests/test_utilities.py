import zipfile
from pathlib import Path
from app.utils.filenames import safe_name, category_for
from app.services.download_service import _extract_zip, _safe_project, _validate_payload
import pytest

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
        zf.writestr("parts/hammer.stl", "solid hammer\n facet normal 0 0 0\n outer loop\n vertex 0 0 0\n vertex 1 0 0\n vertex 0 1 0\n endloop\n endfacet\n endsolid hammer\n")
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

def test_html_error_page_cannot_be_saved_as_stl(tmp_path):
    fake=tmp_path/"model.stl"
    fake.write_text("<!doctype html><title>Sign in</title>")
    with pytest.raises(ValueError,match="unexpected_content_type"):_validate_payload(fake,".stl")

def test_zip_file_cannot_be_classified_as_stl(tmp_path):
    fake=tmp_path/"model.stl"
    with zipfile.ZipFile(fake,"w") as archive:archive.writestr("error.txt","denied")
    with pytest.raises(ValueError,match="plausible STL"):_validate_payload(fake,".stl")

def test_3mf_requires_model_member(tmp_path):
    fake=tmp_path/"model.3mf"
    with zipfile.ZipFile(fake,"w") as archive:archive.writestr("notes.txt","not a model")
    with pytest.raises(ValueError,match="valid 3MF"):_validate_payload(fake,".3mf")
