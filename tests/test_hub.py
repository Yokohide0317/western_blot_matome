import json
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

from wb import hub, project as P
from test_rendering import small_project


def _client():
    # TestClient talks as "testserver"; the hub only answers its own 127.0.0.1 host.
    return TestClient(hub.Hub(), base_url="http://127.0.0.1:8765")


def test_projects_are_served_under_their_id(small_project):
    root, project = small_project
    entry = hub.register(P.locate(root))
    client = _client()
    assert client.get(f"/p/{entry['id']}", follow_redirects=False).headers["location"] == f"/p/{entry['id']}/"
    page = client.get(f"/p/{entry['id']}/")
    assert page.status_code == 200 and "<html" in page.text.lower()
    assert client.get(f"/p/{entry['id']}/api/revision").json() == {"project_revision": project.project_revision}
    assert client.get("/p/0000000000/api/revision").status_code == 404
    assert client.get("/api/hub/projects").json()[0]["id"] == entry["id"]


def test_two_projects_at_once(small_project, tmp_path):
    import shutil

    root, _ = small_project
    other = tmp_path / "other-blot"
    shutil.copytree(root, other)
    first, second = hub.register(P.locate(root)), hub.register(P.locate(other))
    assert first["id"] != second["id"]
    client = _client()
    for entry in (first, second):
        server = client.get(f"/p/{entry['id']}/api/server").json()
        assert server["project_path"] == entry["path"]
    assert [e["id"] for e in hub.recent()] == [second["id"], first["id"]]
    shutil.rmtree(other)
    assert not hub.recent()[0]["exists"]
    assert client.get(f"/p/{second['id']}/api/revision").status_code == 404


def test_requests_from_other_sites_are_refused(small_project):
    entry = hub.register(P.locate(small_project[0]))
    client = _client()
    assert TestClient(hub.Hub()).get("/").status_code == 403  # Host: testserver
    rename = f"/p/{entry['id']}/api/rename_panel"
    assert client.post(rename, json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post(rename, json={}, headers={"Origin": "http://127.0.0.1:8765"}).status_code != 403


def test_render_cache_stays_out_of_the_data_folder(small_project):
    from wb.rendering import render_overview

    root, project = small_project
    image, _ = render_overview(project, root, project.frames[0].frame_id)
    assert image.cache_path.is_relative_to(P.cache_dir(root))
    assert not (root / "wblot_output" / "cache").exists()


def _zip(tmp_path, name="ChemiDoc Images test.zip"):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("propose_fixture", Path(__file__).parent / "fixtures/propose/generate.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    archive = tmp_path / name
    generator.make_zip(archive, two=False)
    return archive


def test_dialog_opens_a_zip_next_to_the_data_with_the_initial_proposal(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    archive = _zip(tmp_path / "data")

    async def chosen(kind):
        assert kind == "file"
        return str(archive)

    monkeypatch.setattr(hub, "choose_path", chosen)
    client = _client()
    result = client.post("/api/hub/open", json={"kind": "file"}).json()
    project_file = P.locate(archive)
    assert project_file.parent.parent == archive.parent  # extracted beside the ZIP
    assert result == {"url": f"/p/{P.project_key(project_file)}/", "id": P.project_key(project_file),
                      "path": str(project_file.resolve()), "created": True}
    assert api_load(project_file).membrane_regions  # the page does not start empty
    again = client.post("/api/hub/open", json={"kind": "file"}).json()
    assert again["url"] == result["url"] and not again["created"]
    assert client.get(result["url"] + "api/revision").status_code == 200


def test_cancel_and_bad_requests(monkeypatch):
    async def cancelled(kind):
        return None

    monkeypatch.setattr(hub, "choose_path", cancelled)
    client = _client()
    assert client.post("/api/hub/open", json={"kind": "folder"}).json() == {"cancelled": True}
    assert client.post("/api/hub/open", json={"kind": "disk"}).status_code == 422
    assert client.post("/api/hub/upload?name=notes.txt", content=b"x").status_code == 422
    assert client.post("/api/hub/nothing", json={}).status_code == 404


def test_dropped_zip_is_kept_in_the_library_and_forget_keeps_files(tmp_path, monkeypatch):
    library = tmp_path / "library"
    monkeypatch.setenv("WBM_LIBRARY", str(library))
    archive = _zip(tmp_path)
    client = _client()
    result = client.post(f"/api/hub/upload?name={archive.name}", content=archive.read_bytes(),
                         headers={"Content-Type": "application/zip"}).json()
    assert (library / archive.name).read_bytes() == archive.read_bytes()
    assert result["path"].startswith(str(library.resolve()))
    second = client.post(f"/api/hub/upload?name={archive.name}", content=archive.read_bytes()).json()
    assert (library / "ChemiDoc Images test (2).zip").is_file() and second["id"] != result["id"]
    assert client.post("/api/hub/forget", json={"id": result["id"]}).json() == {"removed": True}
    assert result["id"] not in [e["id"] for e in hub.recent()]
    assert P.locate(library / archive.name).is_file()


def test_top_page_is_the_static_index():
    page = _client().get("/")
    assert page.status_code == 200 and "ファイルを開く" in page.text and "最近開いたプロジェクト" in page.text


def api_load(path):
    from wb import api

    return api._load(path)
