"""``wbm overview``: the gridded whole image an agent reads membrane positions from."""

import json
from pathlib import Path
import sys

import cv2

from wb import project as P
from wb.cli import main

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "propose"))
import generate  # noqa: E402


def _run(capsys, *args):
    assert main([*args, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_agent_can_place_membranes_from_the_guide_image(tmp_path, capsys):
    archive = tmp_path / "blot.zip"
    generate.make_zip(archive, sessions=1)
    path = Path(_run(capsys, "init", str(archive))["path"])
    opts = ("--project", str(path))

    # Nothing proposed yet: the guide shows the bare reference image with a pixel grid.
    guide = _run(capsys, "overview", *opts)
    assert guide["is_reference"] and guide["membranes"] == [] and guide["selections"] == []
    assert (guide["width"], guide["height"], guide["grid_px"]) == (1100, 900, 100)
    assert {f["modality"] for f in guide["frames"]} == {"chemiluminescence", "colorimetric"}
    image = cv2.imread(guide["path"])
    assert image.shape[:2] == (900 + 26, 1100 + 56)  # axis labels sit in margins, not on the image
    # Regenerable output stays in the cache, never next to the data.
    assert Path(guide["path"]).is_relative_to(P.cache_dir(path))
    assert not list(path.parent.rglob("guide-*.png"))

    # Positions read off the grid go straight into add-membrane on the same frame.
    _run(capsys, "add-membrane", "180", "80", "920", "380", "--rotation", "1", "--frame", guide["frame_id"], *opts)
    _run(capsys, "add-membrane", "180", "480", "920", "780", "--rotation", "-2", *opts)
    checked = _run(capsys, "overview", "--grid", "50", *opts)
    assert checked["grid_px"] == 50
    assert [m["label"] for m in checked["membranes"]] == ["M1", "M2"]
    assert checked["membranes"][0]["polygon"] == [[180, 80], [920, 80], [920, 380], [180, 380]]
    assert len(checked["selections"]) == 2
    # Positive rotation follows bands that rise to the right: the crop's right edge sits higher.
    top_left, top_right = checked["selections"][0]["polygon"][:2]
    assert top_right[1] < top_left[1]
    top_left, top_right = checked["selections"][1]["polygon"][:2]
    assert top_right[1] > top_left[1]


def test_overview_can_show_another_frame_without_changing_the_reference(tmp_path, capsys):
    archive = tmp_path / "blot.zip"
    generate.make_zip(archive, sessions=1)
    path = Path(_run(capsys, "init", str(archive))["path"])
    reference = _run(capsys, "overview", "--project", str(path))
    marker = next(f["frame_id"] for f in reference["frames"] if f["modality"] == "colorimetric")
    other = _run(capsys, "overview", "--frame", marker, "--raw", "--project", str(path))
    assert other["frame_id"] == marker and not other["is_reference"]
    assert Path(other["path"]).is_file() and other["path"] != reference["path"]


def test_empty_proposal_points_the_agent_to_overview(tmp_path, capsys):
    import zipfile

    import numpy as np

    archive = tmp_path / "blank.zip"
    blank = np.full((300, 400, 3), 200, np.uint8)
    with zipfile.ZipFile(archive, "w") as z:
        generate._write(z, "series", "2000-01-01 12h00m00s", blank, "Chemiluminescence")
    path = Path(_run(capsys, "init", str(archive))["path"])
    applied = _run(capsys, "propose", "--apply", "--project", str(path))
    assert applied["panels"] == []
    assert "wbm overview" in applied["next"] and "wbm add-membrane" in applied["next"]
