"""The PPTX "Merge" slide: the whole blot, chemiluminescence × marker, one exposure for all membranes."""

import json
from io import BytesIO
from pathlib import Path
import sys

import numpy as np
from PIL import Image
from pptx import Presentation

from wb import api
from wb.cli import main
from wb.merge import MAX_SATURATED, VISIBLE, _stats, pick

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "propose"))
import generate  # noqa: E402


def test_pick_prefers_the_shortest_exposure_that_shows_every_membrane():
    stats = {
        "short": {"A": (0.7, 0), "B": (0.2, 0)},             # B too faint
        "mid": {"A": (0.9, 0.01), "B": (VISIBLE, 0)},        # both visible, A a little red
        "long": {"A": (1.0, 0.015), "B": (0.8, 0.005)},      # also fine, but longer than needed
    }
    exposure = {"short": 5, "mid": 30, "long": 120}
    assert pick(stats, exposure)[0] == "mid"


def test_pick_shows_the_weakest_membrane_best_when_none_is_clearly_visible():
    stats = {"a": {"A": (0.2, 0), "B": (0.1, 0)}, "b": {"A": (0.5, 0), "B": (0.3, 0)},
             "c": {"A": (0.9, MAX_SATURATED * 3), "B": (0.5, 0)}}  # too much red
    assert pick(stats, {"a": 1, "b": 10, "c": 100})[0] == "b"


def test_pick_falls_back_to_the_least_saturated_exposure():
    stats = {"a": {"A": (0.9, 0.2)}, "b": {"A": (0.9, 0.1)}}
    assert pick(stats, {"a": 1, "b": 2})[0] == "b"


def test_stats_measure_band_contrast_and_red_share_inside_the_crop():
    image = np.full((40, 60, 3), 230, np.uint8)
    image[18:22, 10:50] = 30
    image[19:21, 20:24] = (255, 0, 0)  # saturated display pixels
    polygon = np.array([[5, 10], [55, 10], [55, 30], [5, 30]], float)
    visibility, red = _stats(image, polygon)
    assert visibility > 0.7
    assert 0 < red < 0.02


def _project(tmp_path, capsys):
    archive = tmp_path / "blot.zip"
    generate.make_zip(archive, sessions=1)
    assert main(["init", str(archive), "--json"]) == 0
    path = Path(json.loads(capsys.readouterr().out)["path"])
    assert main(["propose", "--apply", "--project", str(path), "--json"]) == 0
    capsys.readouterr()
    return path


def test_pptx_has_the_merge_slide_after_the_figure(tmp_path, capsys):
    path = _project(tmp_path, capsys)
    result = api.export_project(path, "pptx")
    slides = Presentation(result["path"]).slides
    assert len(slides) == 2
    texts = [s.text_frame.text for s in slides[1].shapes if s.has_text_frame]
    assert texts[0] == "Merge" and "マーカー" in texts[1]
    picture = next(s for s in slides[1].shapes if s.shape_type == 13)
    image = Image.open(BytesIO(picture.image.blob))
    frame = api._load(path).frames[0]
    assert image.size == (frame.width, frame.height)  # the whole, uncropped image
    assert "merge exposure:" in slides[1].notes_slide.notes_text_frame.text


def test_merge_slide_can_be_left_out(tmp_path, capsys):
    path = _project(tmp_path, capsys)
    assert main(["export", "pptx", "--no-merge", "--project", str(path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(Presentation(out["path"]).slides) == 1
