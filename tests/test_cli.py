import json
from pathlib import Path
import time
from urllib.request import urlopen

import pytest

from wb import api, project as P
from wb.cli import main
from test_rendering import small_project


def assert_panel_receipt(result, path, panel_id):
    status = api.status(path)
    assert result == {
        "ok": True,
        "project_revision": status["project_revision"],
        "panel": next(p for p in status["panels"] if p["panel_id"] == panel_id),
    }
    assert not {"sources", "frames", "captures", "transforms", "history"} & result.keys()


def test_validate_cli(small_project, capsys):
    path, _ = small_project
    assert main(["validate", "--project", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["source_issues"] == []


def test_init_status_propose_rename_and_select_real(input_zip, tmp_path, capsys):
    archive = tmp_path / input_zip.name
    archive.symlink_to(input_zip)
    assert main(["init", str(archive), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    path = Path(result["path"])
    assert path == tmp_path / archive.stem / (archive.stem + ".wblot")
    assert path.is_file()
    root = path.parent
    assert not (root / "sources").exists()
    assert all(not p.suffix.lower() in (".tif", ".scn") for p in (root / "wblot_output").rglob("*"))
    assert main(["status", "--project", str(path), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["panels"] == [] and not status["readiness"]["final_allowed"]
    assert main(["propose", "--apply", "--project", str(path), "--json"]) == 0
    proposal = json.loads(capsys.readouterr().out)
    assert set(proposal) == {"ok", "project_revision", "panels", "excluded_image_sets", "warnings_count"}
    assert proposal["ok"] is True
    assert proposal["panels"] == api.status(path)["panels"]
    project = api._load(path)
    assert len(project.panels) == 1 and project.panels[0].label == "A"
    assert main(["rename", "A", "TargetA", "--project", str(path), "--json"]) == 0
    changed = json.loads(capsys.readouterr().out)
    assert_panel_receipt(changed, path, project.panels[0].panel_id)
    assert changed["panel"]["label"] == "TargetA"
    assert main(["init", str(archive), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["project_revision"] == api._load(path).project_revision


def test_default_project_and_errors(small_project, tmp_path, capsys, monkeypatch):
    root, _ = small_project
    monkeypatch.chdir(root)
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["project_revision"] > 0
    (root / "second.wblot").write_text("{}", encoding="utf-8")
    assert main(["status", "--json"]) == 1
    assert "複数" in json.loads(capsys.readouterr().out)["error"]
    (root / "second.wblot").unlink()
    P.locate(root).unlink()
    assert main(["status", "--json"]) == 1
    assert ".wblotがありません" in json.loads(capsys.readouterr().out)["error"]


def test_background_open_reuse_stop(small_project, capsys):
    from wb.server import stop_server

    root, _ = small_project
    args = ["--project", str(root), "--json"]
    try:
        start = time.monotonic()
        assert main(["open", "--no-browser", *args]) == 0
        assert time.monotonic() - start < 8
        first = json.loads(capsys.readouterr().out)
        assert first["url"] == f"{first['hub_url']}p/{P.project_key(root)}/"
        state_path = P.app_home() / "server.json"
        assert json.loads(state_path.read_text(encoding="utf-8"))["pid"] == first["pid"]
        assert first["pid"] and first["token"]
        assert not (root / "wblot_output/server.json").exists()
        with urlopen(first["url"] + "api/revision", timeout=3) as response:
            assert json.load(response)["project_revision"] == api._load(root).project_revision
        assert main(["open", "--no-browser", *args]) == 0
        assert json.loads(capsys.readouterr().out) == first
        assert main(["recent", "--json"]) == 0
        recent = json.loads(capsys.readouterr().out)["projects"]
        assert [e["path"] for e in recent] == [str(P.locate(root).resolve())] and recent[0]["exists"]
        assert main(["stop", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["stopped"]
        assert not state_path.exists()
        with pytest.raises(OSError):
            urlopen(first["url"] + "api/revision", timeout=0.5)
    finally:
        stop_server()


def test_stale_server_state_is_removed():
    from wb.server import stop_server

    path = P.app_home() / "server.json"
    path.write_text(json.dumps({"pid": 2147483647, "port": 9999}), encoding="utf-8")
    assert not stop_server()["stopped"]
    assert not path.exists()


def test_skill_install_into_temporary_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    assert main(["install-skill", "--json"]) == 0
    paths = [Path(p) for p in json.loads(capsys.readouterr().out)["paths"]]
    canonical = Path(__file__).parents[1] / "skills/wblot/SKILL.md"
    assert len(paths) == 3 and any(p.parts[-4:-2] == (".agents", "skills") for p in paths)
    assert all(p.is_relative_to(tmp_path) and p.read_bytes() == canonical.read_bytes() for p in paths)
    before = [p.stat().st_mtime_ns for p in paths]
    assert main(["install-skill", "--json"]) == 0
    capsys.readouterr()
    assert [p.stat().st_mtime_ns for p in paths] == before
    assert main(["install-skill", "--claude", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["paths"]) == 1
    assert (Path(__file__).parents[1] / "src/wb/skills/wblot/SKILL.md").read_bytes() == canonical.read_bytes()


def test_explicit_project_with_multiple_files_and_detached_cli(small_project):
    import subprocess
    import sys

    from wb.server import stop_server

    root, _ = small_project
    path = P.locate(root)
    (root / "other.wblot").write_text("{}", encoding="utf-8")
    command = [sys.executable, "-m", "wb.cli"]
    options = ["--project", str(path), "--json"]
    try:
        start = time.monotonic()
        result = subprocess.run(
            command + ["open", "--no-browser", *options], capture_output=True, text=True, timeout=12
        )
        assert result.returncode == 0, result.stderr
        assert time.monotonic() - start < 8
        opened = json.loads(result.stdout)
        with urlopen(opened["url"] + "api/revision", timeout=3) as response:
            assert json.load(response)["project_revision"] == api._load(path).project_revision
        result = subprocess.run(
            command + ["open", "--no-browser", *options], capture_output=True, text=True, timeout=12
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["pid"] == opened["pid"]
        result = subprocess.run(command + ["stop", "--json"], capture_output=True, text=True, timeout=12)
        assert result.returncode == 0, result.stderr
        assert not (P.app_home() / "server.json").exists()
        with pytest.raises(OSError):
            urlopen(opened["url"] + "api/revision", timeout=0.5)
    finally:
        stop_server()


def test_edit_alignment_and_export_commands(small_project, capsys):
    root, project = small_project
    project = api.confirm_identity_alignment(root, "SET", "F1", "確認用画像", project.project_revision)
    project = api.update_region(
        root, "REGION_K", "SET", "F1", [[5, 5], [40, 5], [40, 30]], 3, "confirmed", project.project_revision
    )
    project = api.update_display_geometry(
        root, "PANEL_K", "F1", 2, [25, 20], {"x0": 10, "x1": 35, "y0": 10, "y1": 25}, 60, {}, project.project_revision
    )
    for transform in project.transforms:
        if transform.kind == "exposure_to_reference":
            transform.status = "candidate"
    P.save_project(project, P.locate(root), project.project_revision)
    options = ["--project", str(root), "--json"]
    assert main(["set-exposure", "PANEL_K", "C2", *options]) == 0
    assert_panel_receipt(json.loads(capsys.readouterr().out), root, "PANEL_K")
    assert main(["adjust", "PANEL_K", "--crop-top", "11", "--width", "80", *options]) == 0
    changed = json.loads(capsys.readouterr().out)
    assert_panel_receipt(changed, root, "PANEL_K")
    geometry = api._load(root).panels[0].display_geometry
    assert geometry.crop_rect.y0 == 11
    assert geometry.target_width_px == 80
    for kind in ("png", "pptx"):
        assert main(["export", kind, *options]) == 0
        result = json.loads(capsys.readouterr().out)
        assert Path(result["path"]).is_file()
        assert Path(result["path"]).parent == root / "wblot_output/exports"


def test_propose_summary_does_not_save_or_print_geometry(small_project, monkeypatch, capsys):
    root, project = small_project
    proposal = json.loads((Path(__file__).parent / 'fixtures/apply/proposal.json').read_text(encoding="utf-8"))
    monkeypatch.setattr(api, 'propose', lambda path: proposal)
    options = ['--project', str(root), '--json']
    assert main(['propose', '--summary', *options]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert set(summary) == {'panels', 'excluded_image_sets', 'warnings_count'}
    assert summary['warnings_count'] == 2
    assert summary['excluded_image_sets'] == proposal['excluded_image_sets']
    assert summary['panels'] == [
        {'label': m['panel_label'], 'membrane_label': m['label'],
         'selected_band_row_candidate_id': m['row']['candidate_id'],
         'selected_exposure_id': m['row']['selected_exposure_id']}
        for g in proposal['groups'] for m in g['membranes']
    ]
    assert api._load(root).project_revision == project.project_revision
    assert main(['propose', *options]) == 0
    assert json.loads(capsys.readouterr().out) == proposal


def test_mutation_text_is_a_short_japanese_receipt(small_project, capsys):
    root, _ = small_project
    assert main(['rename', 'PANEL_K', 'TargetA', '--project', str(root)]) == 0
    output = capsys.readouterr().out
    assert output.startswith('段 TargetA を更新しました（版 ')
    assert len(output.splitlines()) == 1
    assert 'sources' not in output


def test_new_panel_commands(small_project, capsys):
    root, _ = small_project
    opts = ['--project', str(root), '--json']
    def run(*args):
        assert main([*args, *opts]) == 0
        return json.loads(capsys.readouterr().out)
    assert not run('exclude', 'PANEL_L')['panel']['included']
    assert run('include', 'PANEL_L')['panel']['included']
    new = run('duplicate', 'PANEL_K')['panel']
    assert new['included'] and new['review_status'] == 'proposed'
    reordered = run('reorder', new['label'], 'PANEL_L')
    assert [p['panel_id'] for p in reordered['panels']][:2] == [new['panel_id'], 'PANEL_L']
    assert run('lanes', '2', '--samples', 'x,y')['panels'][0]['lane_count'] == 2
    with pytest.raises(SystemExit):
        main(['approve', '--all', *opts])


def test_lanes_and_exports(small_project, capsys):
    root, p = small_project
    p = api.confirm_identity_alignment(root, 'SET', 'F1', '確認', p.project_revision)
    p = api.update_region(root, 'REGION_K', 'SET', 'F1', [[1, 1], [45, 1], [45, 35]], None, 'proposed', p.project_revision)
    p = api.update_display_geometry(root, 'PANEL_K', 'F1', 0, [0, 0], {'x0': 5, 'y0': 5, 'x1': 40, 'y1': 15}, 70, {}, p.project_revision)
    p = api.set_selected_exposure(root, 'PANEL_K', 'C2', p.project_revision)
    opts = ['--project', str(root), '--json']
    for command in (['exclude', 'PANEL_L'], ['lanes', '2', '--samples', 'control,treated']):
        assert main(command + opts) == 0
        result = json.loads(capsys.readouterr().out)
    for kind in ('pptx', 'png'):
        assert main(['export', kind, *opts]) == 0
        assert Path(json.loads(capsys.readouterr().out)['path']).is_file()


def test_add_membrane_and_four_edge_rotation_adjust_cli(small_project, capsys):
    root, _ = small_project
    opts = ['--project', str(root), '--json']
    assert main(['add-membrane', '8', '8', '42', '30', '--rotation', '1.5', '--label', 'ACTB', *opts]) == 0
    added = json.loads(capsys.readouterr().out)
    panel_id = added['panel']['panel_id']
    assert added['panel']['label'] == 'ACTB'
    assert main([
        'adjust', panel_id,
        '--crop-left', '10', '--crop-right', '40',
        '--crop-top', '10', '--crop-bottom', '26',
        '--rotation', '-1.25',
        *opts,
    ]) == 0
    adjusted = json.loads(capsys.readouterr().out)
    assert_panel_receipt(adjusted, root, panel_id)
    saved = next(p for p in api._load(root).panels if p.panel_id == panel_id)
    crop = saved.display_geometry.crop_rect
    assert (crop.x0, crop.x1, crop.y0, crop.y1) == (10, 40, 10, 26)


def test_cli_brand_is_wbm(capsys):
    import importlib.metadata

    from wb.cli import parser

    assert parser().prog == "wbm"
    with pytest.raises(SystemExit):
        parser().parse_args(["--help"])
    assert capsys.readouterr().out.startswith("usage: wbm")
    scripts = {
        (ep.name, ep.value)
        for ep in importlib.metadata.distribution("wbm").entry_points
        if ep.group == "console_scripts"
    }
    assert ("wbm", "wb.cli:main") in scripts
    assert all(name != "wb" for name, _ in scripts)
