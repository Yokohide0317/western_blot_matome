"""Synthetic-image unit tests for wb.alignment."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from wb.alignment import (
    estimate_horizontal_tilt,
    extract_band_signal,
    inspect_rectified_bands,
)
from wb.geometry import Affine, warp


def _make_synthetic_blot(
    width: int = 600,
    height: int = 400,
    rows_y: tuple[int, ...] = (140, 260),
    lane_xs: tuple[int, ...] = (120, 200, 280, 360, 440, 500),
    tilt_deg: float = 0.0,
    band_darkness: int = 180,
    bg_level: int = 235,
    band_rx: int = 20,
    band_ry: int = 5,
    merged: bool = False,
    noise_std: float = 0.0,
    seed: int = 42,
) -> np.ndarray:
    """Create a synthetic RGB blot image with horizontal or tilted bands."""
    base = np.full((height, width, 3), bg_level, dtype=np.uint8)
    fg = int(np.clip(bg_level - band_darkness, 0, 255))

    for y in rows_y:
        if merged:
            x0, x1 = lane_xs[0] - band_rx, lane_xs[-1] + band_rx
            cv2.rectangle(base, (x0, y - band_ry), (x1, y + band_ry), (fg,) * 3, -1)
        else:
            for x in lane_xs:
                cv2.ellipse(base, (x, y), (band_rx, band_ry), 0, 0, 360, (fg,) * 3, -1)

    base = cv2.GaussianBlur(base, (0, 0), 1.5)
    if tilt_deg != 0.0:
        t = Affine.rotation_about((width / 2.0, height / 2.0), tilt_deg, "base", "tilted")
        base = warp(base, t, width, height, border_value=(bg_level,) * 3)

    if noise_std > 0.0:
        rng = np.random.default_rng(seed)
        noisy = base.astype(np.float32) + rng.normal(0.0, noise_std, size=base.shape)
        base = np.clip(np.rint(noisy), 0, 255).astype(np.uint8)
    return base


@pytest.mark.parametrize("tilt_deg", [4.5, -6.0])
def test_tilted_discrete_band_rows(tilt_deg: float):
    img = _make_synthetic_blot(tilt_deg=tilt_deg, band_darkness=190)
    signal = extract_band_signal(img)
    res = estimate_horizontal_tilt(signal)
    assert res["delta_deg"] == pytest.approx(-tilt_deg, abs=0.5)
    assert res["confidence"] >= 0.7
    assert res["method"] in ("centroid_regression+projection", "centroid_regression")
    assert res["metrics"]["row_count"] >= 2


def test_merged_continuous_stripe_tilted():
    img = _make_synthetic_blot(
        rows_y=(200,),
        tilt_deg=3.5,
        band_darkness=170,
        band_ry=7,
        merged=True,
    )
    signal = extract_band_signal(img)
    res = estimate_horizontal_tilt(signal)
    assert res["delta_deg"] == pytest.approx(-3.5, abs=0.5)
    assert res["confidence"] >= 0.5
    assert "projection" in res["method"] or "centroid_regression" in res["method"]


def test_faint_bands_low_contrast():
    # Peak signal ~0.08 after background subtraction (darkness ~25/255 = 0.098)
    img = _make_synthetic_blot(
        rows_y=(150, 250),
        tilt_deg=-4.0,
        band_darkness=26,
        bg_level=235,
        noise_std=1.0,
    )
    signal = extract_band_signal(img)
    assert 0.04 <= float(signal.max()) <= 0.15
    res = estimate_horizontal_tilt(signal)
    assert res["delta_deg"] == pytest.approx(4.0, abs=0.5)
    assert res["confidence"] >= 0.5
    assert res["method"] != "insufficient_signal"


def test_blank_and_pure_noise_image():
    blank = np.full((300, 500, 3), 235, dtype=np.uint8)
    res_blank = estimate_horizontal_tilt(extract_band_signal(blank))
    assert res_blank["delta_deg"] == 0.0
    assert res_blank["confidence"] == 0.0
    assert res_blank["method"] == "insufficient_signal"

    rng = np.random.default_rng(123)
    noise = np.clip(235 + rng.normal(0.0, 3.0, size=(300, 500, 3)), 0, 255).astype(np.uint8)
    res_noise = estimate_horizontal_tilt(extract_band_signal(noise))
    assert res_noise["delta_deg"] == 0.0
    assert res_noise["confidence"] < 0.2
    assert res_noise["method"] == "insufficient_signal"


def test_vertical_smears_in_horizontal_lanes_no_spurious_tilt():
    img = _make_synthetic_blot(
        rows_y=(200,),
        lane_xs=(140, 220, 300, 380, 460),
        tilt_deg=0.0,
        band_darkness=180,
        band_rx=6,
        band_ry=40,
    )
    signal = extract_band_signal(img)
    res = estimate_horizontal_tilt(signal)
    assert abs(res["delta_deg"]) < 1.0


def test_inspect_rectified_bands():
    img = _make_synthetic_blot(
        rows_y=(110, 220, 310),
        lane_xs=(120, 210, 300, 390, 480),
        tilt_deg=0.0,
        band_darkness=180,
    )
    # Add a small red saturation region on the top row
    img[108:113, 295:305] = (255, 0, 0)
    sat_mask = (img[..., 0] > 240) & (img[..., 1] < 20) & (img[..., 2] < 20)
    signal = extract_band_signal(img)
    info = inspect_rectified_bands(signal, saturation_mask=sat_mask)

    assert len(info["row_positions_y"]) == 3
    for detected_y, expected_y in zip(info["row_positions_y"], (110.0, 220.0, 310.0)):
        assert detected_y == pytest.approx(expected_y, abs=2.5)
    assert info["saturation_fraction"] == pytest.approx(float(np.mean(sat_mask)), abs=1e-6)
    assert info["saturation_fraction"] > 0.0
    assert abs(info["residual_tilt_deg"]) < 0.5
    assert len(info["lane_peaks_x"]) >= 4
    assert all(p > 0.05 for p in info["peak_signals"])
