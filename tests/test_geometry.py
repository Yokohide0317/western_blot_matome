import math

import cv2
import numpy as np
import pytest

from wb.geometry import (
    Affine, OutOfFrameError, Rect, crop_polygon_in_source, fit_lane_centers,
    lane_crop_x, lane_spacing_residuals, marker_panel_transform, panel_transform,
    pixel_center, polygon_within_frame, require_within_frame, to_opencv_matrix, warp,
)


def chain():
    a = (Affine.translation(14, -3, "a1", "reference") @
         Affine.rotation_about((23, 31), 17, "a0", "a1") @
         Affine.scaling(1.2, "source", "a0"))
    d = Affine.rotation_about((45, 50), -23, "reference", "rectified")
    return a, d


def test_roundtrip_and_oblique_crop():
    a, d = chain()
    crop = Rect(10, 15, 90, 60)
    panel = panel_transform(a, d, crop, 217, "panel")
    points = np.random.default_rng(104).uniform(-200, 300, (1000, 2))
    np.testing.assert_allclose(panel.transform.inverse().apply(panel.transform.apply(points)), points, atol=1e-6)
    polygon = crop_polygon_in_source(a, d, crop)
    assert not np.isclose(polygon[0, 1], polygon[1, 1])
    np.testing.assert_allclose((d @ a).apply(polygon), crop.corners(), atol=1e-12)
    assert panel.transform.from_frame_id == "source"
    assert panel.transform.to_frame_id == "panel"


def test_frames_and_matrix_validation():
    with pytest.raises(ValueError, match="frame mismatch"):
        Affine.identity("a") @ Affine.identity("b")
    with pytest.raises(ValueError, match="last row"):
        Affine("a", "b", [[1, 0, 0], [0, 1, 0], [0, 1, 1]])
    with pytest.raises(ValueError, match="singular"):
        Affine.scaling(0, "a", "b").inverse()
    with pytest.raises(ValueError, match="frame mismatch"):
        panel_transform(Affine.identity("a"), Affine.identity("b"), Rect(0, 0, 1, 1), 10, "p")
    matrix = np.eye(3)
    affine = Affine("a", "b", matrix)
    matrix[0, 0] = 4
    assert affine.matrix[0, 0] == 1
    with pytest.raises(ValueError):
        affine.matrix[0, 0] = 4


def test_rotation_sign():
    t = Affine.rotation_about((5, 5), 90, "a", "b")
    np.testing.assert_allclose(t.apply((6, 5)), (5, 6), atol=1e-12)


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
@pytest.mark.parametrize("channels", [1, 3])
@pytest.mark.parametrize("rotate", [False, True])
def test_warp_forward_pixel(dtype, channels, rotate):
    image = np.zeros((31, 31) if channels == 1 else (31, 31, 3), dtype=dtype)
    row, col = 8, 12
    image[row, col] = 200
    t = (Affine.rotation_about((15.5, 15.5), 90, "src", "dst") if rotate else
         Affine.translation(10, 5, "src", "dst"))
    result = warp(image, t, 31, 31, interpolation=cv2.INTER_NEAREST)
    x, y = t.apply(pixel_center(row, col))
    expected = np.zeros_like(image)
    expected[round(y - .5), round(x - .5)] = 200
    np.testing.assert_array_equal(result, expected)


def test_warp_scale_half_pixel_adapter():
    image = np.zeros((8, 8), dtype=np.uint8)
    image[2, 3] = 192
    t = Affine.scaling(2, "src", "dst")
    np.testing.assert_allclose(to_opencv_matrix(t.matrix), [[2, 0, .5], [0, 2, .5], [0, 0, 1]])
    result = warp(image, t, 16, 16, interpolation=cv2.INTER_LINEAR)
    x, y = t.apply(pixel_center(2, 3))
    assert (x, y) == (7, 5)
    # The mapped center lies at the intersection of four destination pixels.
    np.testing.assert_array_equal(result[4:6, 6:8], np.full((2, 2), 108))


def test_bounds_and_rect():
    rect = Rect(0, 0, 10, 20)
    np.testing.assert_array_equal(rect.corners(), [[0, 0], [10, 0], [10, 20], [0, 20]])
    assert polygon_within_frame(rect.corners(), 10, 20)
    polygon = rect.corners()
    polygon[1, 0] += 1e-8
    assert not polygon_within_frame(polygon, 10, 20)
    with pytest.raises(OutOfFrameError):
        require_within_frame(polygon, 10, 20)
    with pytest.raises(ValueError):
        Rect(0, 0, 0, 1)


def test_panel_size_and_marker_alignment():
    a = Affine.identity("source", "reference")
    d = Affine.identity("reference", "rectified")
    crop = Rect(2, 5, 9, 8.2)
    panel = panel_transform(a, d, crop, 10, "panel")
    assert panel.out_width == 10
    assert panel.scale == 10 / 7
    assert panel.content_height == pytest.approx(3.2 * 10 / 7)
    assert panel.out_height == math.ceil(panel.content_height)
    np.testing.assert_allclose(panel.transform.matrix[:2, :2], np.eye(2) * panel.scale)
    with pytest.raises(ValueError):
        panel_transform(a, d, crop, 10.0, "panel")
    marker_crop = Rect(20, 5, 24.1, 8.2)
    marker = marker_panel_transform(Affine.identity("marker", "reference"), d, marker_crop, panel.scale, "marker_panel")
    assert marker.out_width == math.ceil(4.1 * panel.scale)
    assert marker.out_height == panel.out_height
    assert marker.scale == panel.scale
    assert marker.transform.apply((22, 6))[1] == pytest.approx(panel.transform.apply((4, 6))[1])


def test_lane_fitting_and_residuals():
    np.testing.assert_allclose(fit_lane_centers(5, {2: 20, 4: 40}), [10, 20, 30, 40, 50])
    known = {1: 10, 2: 21, 3: 29, 4: 40}
    fit = fit_lane_centers(4, known)
    np.testing.assert_allclose(fit, [10.3, 20.1, 29.9, 39.7])
    residuals = lane_spacing_residuals(list(known.values()))
    np.testing.assert_allclose(residuals, [-.3, .9, -.9, .3], atol=1e-12)
    np.testing.assert_allclose(lane_crop_x([10, 20, 30]), [5, 35])
    np.testing.assert_allclose(lane_crop_x([10], pitch=8), [6, 14])
    with pytest.raises(ValueError):
        lane_crop_x([10])
    for count, entries in [(0, {1: 1, 2: 2}), (3, {1: 1}), (3, {1: 1, 4: 4})]:
        with pytest.raises(ValueError):
            fit_lane_centers(count, entries)


def test_json_roundtrip():
    a, _ = chain()
    record = a.to_json("TR_1", "exposure_to_reference", "confirmed", 3, {"method": "manual"})
    assert set(record) == {"transform_id", "kind", "from_frame_id", "to_frame_id", "matrix", "status", "evidence", "revision"}
    b = Affine.from_json(record)
    assert (b.from_frame_id, b.to_frame_id) == (a.from_frame_id, a.to_frame_id)
    np.testing.assert_array_equal(b.matrix, a.matrix)
    assert b.to_json("TR_1", "exposure_to_reference", "confirmed", 3, {"method": "manual"}) == record
