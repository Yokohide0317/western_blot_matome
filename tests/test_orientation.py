"""Orientation regressions using only synthetic images and project geometry."""
from io import BytesIO
import json

import cv2
import numpy as np
from PIL import Image
from pptx import Presentation
import pytest
import tifffile

from wb import api, export, project as P
from wb.app import create_app
from wb.cli import main
from wb.geometry import Affine, Rect, crop_polygon_in_source, warp
from wb.propose import _orientation, _signal
from wb.rendering import render_panel
from fastapi.testclient import TestClient
from test_rendering import small_project


def wide_membrane(degrees=90, smear=False):
    image = np.full((600, 800, 3), 40, np.uint8)
    polygon = Rect(40, 60, 760, 540).corners()
    cv2.rectangle(image, (40, 60), (760, 540), (235,) * 3, -1)
    # The membrane outline stays horizontal; rotate only the sample row.
    centers = np.array([[280, 300], [360, 300], [440, 300], [520, 300]])
    signal_image = np.full_like(image, 235)
    for x, y in centers:
        cv2.ellipse(signal_image, (int(x), int(y)), (18, 6), 0, 0, 360, (25,) * 3, -1)
    turn = Affine.rotation_about((400, 300), degrees, 'base', 'ref')
    if smear:
        signal_image[:] = 235
        for x, y in centers:
            cv2.ellipse(signal_image, (int(x), int(y)), (6, 35), 0, 0, 360, (25,) * 3, -1)
        turn = Affine.identity('base', 'ref')
    turned = warp(signal_image, turn, 800, 600, border_value=(235,) * 3)
    image[60:541, 40:761] = turned[60:541, 40:761]
    return image, polygon, turn.apply(centers)


@pytest.mark.parametrize('degrees', [70, 90, 110])
def test_wide_membrane_uses_vertical_band_row_instead_of_outline(degrees):
    image, polygon, centers = wide_membrane(degrees)
    rotation, method, evidence = _orientation(polygon, image, _signal(image), 'M1', [])
    assert rotation == pytest.approx(-degrees, abs=1)
    assert evidence['aligned_component_count'] == 4
    assert evidence['orientation_ambiguity_degrees'] == 180
    rectified = Affine.rotation_about((400, 300), rotation, 'ref', 'rect').apply(centers)
    assert np.ptp(rectified[:, 1]) < 5


def test_vertical_smears_in_horizontal_lanes_do_not_force_a_quarter_turn():
    image, polygon, _ = wide_membrane(smear=True)
    rotation, _, _ = _orientation(polygon, image, _signal(image), 'M1', [])
    assert abs(rotation) < 1


def test_auto_orientation_reaches_preview_and_pptx(tmp_path):
    image, _, _ = wide_membrane(100)
    for modality in ('Colorimetric', 'Chemiluminescence'):
        tifffile.imwrite(tmp_path / f'60.000s({modality}).tif', image, photometric='rgb')
    initial = api.import_project(tmp_path)
    proposal = api.propose(tmp_path)
    membrane = proposal['groups'][0]['membranes'][0]
    assert membrane['rotation']['degrees'] == pytest.approx(-100, abs=1)
    saved = api.apply_proposal(tmp_path, proposal, initial.project_revision)
    panel = saved.panels[0]
    rendered = render_panel(saved, tmp_path, panel.panel_id, panel.selected_exposure_id)
    assert rendered.width > rendered.height * 2
    pixels = np.array(Image.open(BytesIO(rendered.png)))
    n, _, stats, centers = cv2.connectedComponentsWithStats((pixels[..., 0] < 80).astype('uint8'), 8)
    centers = np.asarray([centers[i] for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 30])
    assert len(centers) == 4
    assert np.ptp(centers[:, 1]) < 5
    destination = export.export_pptx(saved, tmp_path, tmp_path / 'figure.pptx', merge=False)
    pictures = [shape for shape in Presentation(destination).slides[0].shapes if shape.shape_type == 13]
    assert any(shape.image.blob == rendered.png for shape in pictures)
    assert api.validate(tmp_path)['source_issues'] == []


def selected_project(small_project, angle=0):
    root, project = small_project
    return root, api.update_display_geometry(root, 'PANEL_K', 'F1', angle, [25, 20],
        {'x0': 20, 'y0': 6, 'x1': 30, 'y1': 34}, 60,
        {'lane_count': 3, 'centers_x': [22, 25, 28], 'boundaries': [0, .2, .6, 1], 'status': 'confirmed'},
        project.project_revision)


def footprint(project):
    dg = project.panels[0].display_geometry
    record = next(t for t in project.transforms if t.transform_id == dg.reference_to_rectified_transform_id)
    points = crop_polygon_in_source(Affine.identity('F1'), Affine.from_json(record.model_dump()), Rect(**dg.crop_rect.model_dump()))
    return np.array(sorted(np.round(points, 10).tolist()))


@pytest.mark.parametrize('angle', [0, 7, -103])
@pytest.mark.parametrize('degrees', [-90, 90, 180])
def test_turn_preserves_selected_pixels_and_is_undoable(small_project, angle, degrees):
    root, before = selected_project(small_project, angle)
    after = api.rotate_panel(root, 'PANEL_K', degrees, before.project_revision)
    np.testing.assert_allclose(footprint(after), footprint(before), atol=1e-10)
    assert after.project_revision == before.project_revision + 1
    assert after.panels[1] == before.panels[1]
    old = before.panels[0].display_geometry.crop_rect
    new = after.panels[0].display_geometry.crop_rect
    if degrees != 180:
        assert new.x1 - new.x0 == pytest.approx(old.y1 - old.y0)
        assert new.y1 - new.y0 == pytest.approx(old.x1 - old.x0)
        layout = after.panels[0].display_geometry.lane_layout
        assert layout.lane_count == 3 and not layout.centers_x and not layout.boundaries
    restored = api.undo(root, after.project_revision)
    assert restored.panels == before.panels


def test_turn_cli_and_http_use_the_same_geometry(small_project, capsys):
    root, before = selected_project(small_project)
    assert main(['rotate', 'K', '--degrees', '-90', '--project', str(root), '--json']) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['ok'] and receipt['project_revision'] == before.project_revision + 1
    rotated = api._load(root)
    assert render_panel(rotated, root, 'PANEL_K', 'C1').width > render_panel(rotated, root, 'PANEL_K', 'C1').height
    with TestClient(create_app(root)) as client:
        response = client.post('/api/rotate_panel', json={
            'panel_id': 'PANEL_K', 'degrees': 90, 'expected_revision': rotated.project_revision})
        assert response.status_code == 200, response.text
    np.testing.assert_allclose(footprint(api._load(root)), footprint(before), atol=1e-10)


def test_invalid_turn_does_not_save(small_project):
    root, before = selected_project(small_project)
    data = P.locate(root).read_bytes()
    with pytest.raises(ValueError, match='90'):
        api.rotate_panel(root, 'PANEL_K', 45, before.project_revision)
    assert P.locate(root).read_bytes() == data
