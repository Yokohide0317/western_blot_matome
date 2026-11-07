import json
import zipfile

import pytest

from wb import config, ingest
from wb.cli import main


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("WBM_CONFIG", str(path))
    return path


def test_defaults_read_chemidoc_names(config_file):
    rules = config.naming()
    assert rules.pattern.pattern == config.DEFAULT_PATTERN
    assert rules.parse("2000-01-01 10h00m00s 0.200s(Colorimetric).tif") == {
        "exposure_s": 0.2, "modality": "colorimetric", "mode": "Colorimetric",
        "scn_name": "2000-01-01 10h00m00s 0.200s.scn"}
    assert rules.parse("x 660.000s(Chemiluminescence).raw16.tif")["modality"] == "chemiluminescence"
    assert rules.is_raw16("x 660.000s(Chemiluminescence).raw16.tif")
    assert rules.parse("photo.tif") is None


def test_init_writes_a_template_that_matches_the_defaults(config_file):
    assert config.init() == {"path": str(config_file), "created": True}
    assert config.init()["created"] is False
    assert config.show()["exists"] and config.naming().pattern.pattern == config.DEFAULT_PATTERN


def test_a_lab_with_different_names(config_file, tmp_path):
    config_file.write_text('''
[naming]
pattern = '(?P<stem>.*)_(?P<mode>Chemi|Marker)_(?P<exposure>\\d+)ms$'
exposure_scale = 0.001
signal_modes = ["Chemi"]
marker_modes = ["Marker"]
raw16_suffix = "_16bit"
''', encoding="utf-8")
    rules = config.naming()
    assert rules.parse("blot1_Chemi_1500ms.tif") == {"exposure_s": 1.5, "modality": "chemiluminescence",
                                                    "mode": "Chemi", "scn_name": "blot1.scn"}
    assert rules.parse("blot1_Chemi_1500ms_16bit.tif")["exposure_s"] == 1.5
    assert rules.raw16_name("blot1_Chemi_1500ms.tif") == "blot1_Chemi_1500ms_16bit.tif"
    assert rules.parse("blot1_Marker_200ms.tif")["modality"] == "colorimetric"
    archive = tmp_path / "lab.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name in ("run/blot1_Chemi_1500ms.tif", "run/blot1_Marker_200ms.tif", "run/notes.tif"):
            z.writestr(name, b"")
    result = config.check(archive)
    assert (result["signal"], result["marker"], result["unrecognised"]) == (1, 1, ["run/notes.tif"])


@pytest.mark.parametrize("body, message", [
    ("[naming]\npattern = '(?P<exposure>\\d+)s'\n", "mode"),
    ("[naming]\npattern = '('\n", "正規表現"),
    ("[naming]\nsignal_modes = ['A']\nmarker_modes = ['a']\n", "重ならない"),
    ("[naming]\nexposure_scale = 0\n", "正の数"),
    ("[naming]\ncolour = 1\n", "不明な項目"),
    ("[other]\n", "不明な項目"),
    ("not toml = = 1", "読めません"),
])
def test_bad_settings_are_explained(config_file, body, message):
    config_file.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        config.naming()


def test_cli_config_commands(config_file, tmp_path, capsys):
    assert main(["config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["exists"] is False
    assert main(["config", "--init", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["created"] is True
    folder = tmp_path / "images"
    folder.mkdir()
    (folder / "a 1.000s(Chemiluminescence).tif").write_bytes(b"")
    assert main(["config", "--check", str(folder)]) == 0
    out = capsys.readouterr().out
    assert "発光" in out and "画像 1 件" in out


def test_import_uses_the_configured_names(config_file, tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("propose_fixture", Path(__file__).parent / "fixtures/propose/generate.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    standard = tmp_path / "standard.zip"
    generator.make_zip(standard, two=False)
    renamed = tmp_path / "renamed.zip"
    with zipfile.ZipFile(standard) as src, zipfile.ZipFile(renamed, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename.replace("(Chemiluminescence)", "(Chemi)").replace("(Colorimetric)", "(Mark)"),
                         src.read(info))
    before = {(s.modality, s.exposure_seconds) for s in ingest.scan_archive(standard).sources if s.modality}
    assert {(s.modality, s.exposure_seconds) for s in ingest.scan_archive(renamed).sources if s.modality} == set()
    config_file.write_text('[naming]\nsignal_modes = ["Chemi"]\nmarker_modes = ["Mark"]\n', encoding="utf-8")
    assert {(s.modality, s.exposure_seconds) for s in ingest.scan_archive(renamed).sources if s.modality} == before


def test_icloud_only_files_stop_with_a_clear_message(tmp_path, monkeypatch):
    from wb import api, project as P

    archive = tmp_path / "cloud.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("run/a 1.000s(Chemiluminescence).tif", b"")
    monkeypatch.setattr(P, "is_cloud_only", lambda path: True)
    with pytest.raises(ValueError, match="iCloud Drive"):
        config.check(archive)
    with pytest.raises(ValueError, match="今すぐダウンロード"):
        api.import_project(archive)
    (tmp_path / "bad.zip").write_bytes(b"not a zip")
    monkeypatch.setattr(P, "is_cloud_only", lambda path: False)
    with pytest.raises(ValueError, match="ZIPとして読めません"):
        config.check(tmp_path / "bad.zip")
