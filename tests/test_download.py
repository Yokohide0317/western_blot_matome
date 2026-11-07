import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from wb.app import create_app

_spec = importlib.util.spec_from_file_location("export_fixture", Path(__file__).parent / "fixtures/export/generate.py")
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


def test_export_then_download_only_from_the_exports_folder(tmp_path):
    _fixture.make_project(tmp_path)
    client = TestClient(create_app(tmp_path))
    for kind, mime in (("pptx", "presentation"), ("png", "image/png")):
        result = client.post("/api/export", json={"kind": kind}).json()
        name, = result["files"]
        assert name == Path(result["path"]).name
        response = client.get(f"/api/download/{name}")
        assert response.status_code == 200
        assert mime in response.headers["content-type"]
        from urllib.parse import quote
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment") and (f'filename="{name}"' in disposition or quote(name) in disposition)
        assert response.content == Path(result["path"]).read_bytes()
    assert client.get("/api/download/missing.png").status_code == 404
    assert client.get("/api/download/fixture.wblot").status_code == 422
    assert client.get("/api/download/..%2Ffixture.wblot").status_code in (404, 422)
