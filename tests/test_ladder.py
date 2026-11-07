import numpy as np
import pytest

from wb.geometry import Affine, Rect
from wb.layout import LABEL_MAX_W, LABEL_W, label_width
from wb.propose import choose_ladder, find_ladder_columns


def _membrane(ladders=(), edge_x=None):
    """White membrane on a grey tray, with ladder lanes and an optional dark edge line."""
    image = np.full((400, 600, 3), 150, np.uint8)
    image[50:350, 50:550] = 245
    for x in ladders:
        for y in range(90, 330, 30):
            image[y:y + 5, x - 15:x + 15] = 90
    if edge_x is not None:
        image[50:350, edge_x:edge_x + 3] = 60
    return image


POLY = Rect(50, 50, 550, 350).corners()
IDENTITY = Affine.identity("F")


def test_finds_interior_ladders_and_ignores_edge_lines():
    columns = find_ladder_columns(_membrane(ladders=(120, 300), edge_x=60), IDENTITY, 600, 400, POLY)
    centers = sorted(c["center"] for c in columns)
    assert len(centers) == 2
    assert centers == pytest.approx([120, 300], abs=4)


def test_prefers_the_ladder_nearest_the_samples():
    image = _membrane(ladders=(80, 300, 520))
    chosen = choose_ladder(image, IDENTITY, 600, 400, POLY, prefer_x=330)
    assert chosen["x0_rectified"] < 300 < chosen["x1_rectified"]
    assert chosen["x1_rectified"] - chosen["x0_rectified"] < 60


def test_no_ladder_returns_none():
    assert choose_ladder(_membrane(edge_x=60), IDENTITY, 600, 400, POLY, prefer_x=300) is None


def test_label_column_fits_long_labels():
    assert label_width(["A"]) == LABEL_W
    assert label_width(["TargetA→Actin?"]) > LABEL_W
    assert label_width(["抗体名がとても長いラベルの例です"]) == LABEL_MAX_W
