"""Selection-based raw-pixel placement and CLI integration, using synthetic data."""
import json
from pathlib import Path

import numpy as np
import pytest

from wb import api, project as P, quantification as Q
from wb.cli import main
from wb.geometry import Affine
from wb.model import Frame, Source, Transform
from test_quantification_api import quant_project, start, post, group_of, compute


def select(root, *, rotation=0, boundaries=None, spans=None, centers=None, crop=None, reference='F1'):
    stored = api._load(root)
    layout = {'lane_count': 3}
    if boundaries is not None:
        layout['boundaries'] = boundaries
    if spans is not None:
        layout['spans'] = spans
    if centers is not None:
        layout['centers_x'] = centers
    stored.membrane_regions[0].reference_frame_id = reference
    stored = api._apply_display_geometry(stored, 'PANEL_K', reference, rotation, [48, 32],
        crop or {'x0': 6, 'y0': 24, 'x1': 84, 'y1': 34}, 600, layout)
    return P.save_project(stored, P.locate(root), stored.project_revision).model_dump(mode='json')


def centers_of(group):
    return np.array([[l['x'] + group['width_px']/2, l['y'] + group['height_px']/2] for l in group['lanes']])


@pytest.mark.parametrize('layout,expected_x', [
    ({'boundaries': [0, .2, .6, 1]}, [13.8, 37.2, 68.4]),
    ({'spans': [(0, .2), (.3, .5), (.8, 1)]}, [13.8, 37.2, 76.2]),
    ({'centers': [14, 42, 75]}, [14, 42, 75]),
    ({}, [19, 45, 71]),
])
def test_selection_uses_lane_boundaries_gaps_centers_and_crop(quant_project, layout, expected_x):
    root, client, _, pixels, source = quant_project
    raw_before = source.read_bytes()
    project = select(root, **layout)
    project, group = start(client, project)
    assert group['placement_method'] == 'band_selection'
    np.testing.assert_allclose(centers_of(group), np.array([[x, 29] for x in expected_x]), atol=.5)
    assert group['height_px'] == 8
    stored = api._load(root)
    Q.validate(pixels, stored.quantification_groups[0])
    assert source.read_bytes() == raw_before
    project = compute(client, project, group['group_id'])
    for lane, row in zip(group['lanes'], group_of(project)['results']['rows']):
        assert row['raw_sum'] == float(pixels[lane['y']:lane['y']+group['height_px'], lane['x']:lane['x']+group['width_px']].sum())


def test_rotation_is_undone_and_replacement_clears_results(quant_project):
    root, client, project, pixels, _ = quant_project
    project = select(root, rotation=18)
    project, group = start(client, project)
    expected = Affine.rotation_about([48, 32], -18, 'rect', 'raw').apply([[19, 29], [45, 29], [71, 29]])
    np.testing.assert_allclose(centers_of(group), expected, atol=.5)
    project = compute(client, project, group['group_id'])
    lanes = group_of(project)['lanes']
    lanes[0]['excluded'] = True
    project = post(client, 'update_quantification', project, group_id=group['group_id'], lanes=lanes, signal_polarity='low')
    project = select(root, rotation=-12)
    project = post(client, 'place_quantification', project, group_id=group['group_id'])
    placed = group_of(project)
    expected = Affine.rotation_about([48, 32], 12, 'rect', 'raw').apply([[19, 29], [45, 29], [71, 29]])
    np.testing.assert_allclose(centers_of(placed), expected, atol=.5)
    assert placed['results'] is None and placed['lanes'][0]['excluded']
    assert placed['signal_polarity'] == 'low'
    Q.validate(pixels, api._load(root).quantification_groups[0])


def pair_display(root, *, width=48, height=32, explicit=False, different_exposure=False):
    import hashlib
    import tifffile
    stored = api._load(root)
    path = root / 'synthetic-display.tif'
    tifffile.imwrite(path, np.zeros((height, width, 3), dtype=np.uint8), photometric='rgb')
    stored.sources.append(Source(source_asset_id='DISPLAY', representation_kind='presentation_export_candidate',
        relative_path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size,
        image={'width': width, 'height': height, 'dtype': 'uint8', 'channels': 'RGB'}, quant_eligibility='presentation_only'))
    stored.frames.append(Frame(frame_id='DISPLAY_FRAME', source_asset_id='DISPLAY', width=width, height=height, page_index=0))
    capture = stored.captures[1 if different_exposure else 0]
    capture.presentation_source_asset_id = 'DISPLAY'
    if explicit:
        t = Affine('F1', 'DISPLAY_FRAME', [[.5, 0, 1], [0, .5, 2], [0, 0, 1]])
        stored.transforms.append(Transform.model_validate(t.to_json('RAW_DISPLAY', 'analysis_to_presentation', 'confirmed', 1)))
        capture.analysis_to_presentation_transform_id = 'RAW_DISPLAY'
    P.save_project(stored, P.locate(root), stored.project_revision)


@pytest.mark.parametrize('explicit', [False, True])
def test_paired_display_scaling_and_explicit_transform(quant_project, explicit):
    root, client, _, _, _ = quant_project
    pair_display(root, explicit=explicit)
    project = select(root, reference='DISPLAY_FRAME', crop={'x0': 3, 'y0': 12, 'x1': 42, 'y1': 17})
    project, group = start(client, project)
    expected = np.array([[19, 29], [45, 29], [71, 29]]) - ([2, 4] if explicit else [0, 0])
    np.testing.assert_allclose(centers_of(group), expected, atol=.5)
    assert group['placement_method'] == ('band_selection' if explicit else 'estimated')
    if not explicit:
        assert '推定' in group['placement_note']
    assert group['results'] is None


def test_selected_exposure_requires_alignment_then_maps_back_to_its_raw(quant_project):
    root, client, _, _, _ = quant_project
    pair_display(root, different_exposure=True)
    # Give the reference a separate frame ID so it is not the target raw frame.
    stored = api._load(root)
    stored.frames.append(Frame(frame_id='REFERENCE', source_asset_id='DISPLAY', width=48, height=32))
    P.save_project(stored, P.locate(root), stored.project_revision)
    project = select(root, reference='REFERENCE', crop={'x0': 6, 'y0': 12, 'x1': 39, 'y1': 17})
    # Only one presentation page may be used; remove the duplicate source link.
    stored = api._load(root)
    stored.frames[-1].source_asset_id = None
    P.save_project(stored, P.locate(root), stored.project_revision)
    project = client.get('/api/project').json()
    project, group = start(client, project, capture_id='C2')
    assert group['placement_method'] == 'fallback'
    stored = api._load(root)
    t = Affine.translation(2, -1, 'DISPLAY_FRAME', 'REFERENCE')
    stored.transforms.append(Transform.model_validate(t.to_json('EXPOSURE', 'exposure_to_reference', 'confirmed', 1)))
    P.save_project(stored, P.locate(root), stored.project_revision)
    project = client.get('/api/project').json()
    project = post(client, 'place_quantification', project, group_id=group['group_id'])
    np.testing.assert_allclose(centers_of(group_of(project)), [[19, 31], [41, 31], [63, 31]], atol=.5)


def test_unusable_geometry_keeps_existing_rois_on_explicit_replacement(quant_project):
    root, client, _, _, _ = quant_project
    pair_display(root, height=40)
    project = select(root, reference='DISPLAY_FRAME', crop={'x0': 3, 'y0': 12, 'x1': 42, 'y1': 17})
    project, group = start(client, project)
    assert group['placement_method'] == 'fallback' and '縦横比' in group['placement_note']
    response = client.post('/api/place_quantification', json={'group_id': group['group_id'], 'expected_revision': project['project_revision']})
    assert response.status_code == 422
    assert client.get('/api/project').json() == project


@pytest.mark.parametrize('top,bottom', [(0, 8), (56, 64)])
def test_edge_bands_get_in_bounds_backgrounds_on_available_side(quant_project, top, bottom):
    root, client, _, pixels, _ = quant_project
    project = select(root, crop={'x0': 0, 'y0': top, 'x1': 96, 'y1': bottom})
    project, group = start(client, project)
    assert group['placement_method'] == 'band_selection'
    for lane in group['lanes']:
        assert 0 <= lane['x'] <= 96-group['width_px']
        assert 0 <= lane['background_y'] <= 64-group['height_px']
        assert (lane['background_y'] > lane['y']) == (top == 0)
    Q.validate(pixels, api._load(root).quantification_groups[0])


def test_reposition_rejects_stale_revision_and_changed_lane_count(quant_project):
    root, client, _, _, _ = quant_project
    project = select(root)
    project, group = start(client, project)
    old_revision = project['project_revision']
    project = compute(client, project, group['group_id'])
    response = client.post('/api/place_quantification', json={'group_id': group['group_id'], 'expected_revision': old_revision})
    assert response.status_code == 409
    assert client.get('/api/project').json() == project
    saved = api.set_lanes(root, 2, project['project_revision'], panel_id='PANEL_K')
    before = client.get('/api/project').json()
    response = client.post('/api/place_quantification', json={'group_id': group['group_id'], 'expected_revision': saved.project_revision})
    assert response.status_code == 422
    assert client.get('/api/project').json() == before


def test_cli_start_edit_compute_export_and_reposition(quant_project, capsys, tmp_path):
    root, client, _, _, source = quant_project
    select(root)
    def command(*args, ok=0):
        assert main(['quantify', *args, '--project', str(root), '--json']) == ok
        return json.loads(capsys.readouterr().out)
    started = command('start', 'TargetA')
    group = started['group']
    gid = group['group_id']
    assert group['placement_method'] == 'band_selection'
    assert 'sources' not in started
    assert command('show')['groups'][0]['group_id'] == gid
    assert command('show', gid)['results_current'] is False
    assert 'error' in command('export', gid, ok=1)
    settings = tmp_path / 'settings.json'
    group['lanes'][0]['excluded'] = True
    settings.write_text(json.dumps({'lanes': group['lanes'], 'saturation_value': 60000}), encoding='utf-8')
    command('update', gid, '--settings', str(settings))
    command('compute', gid)
    assert command('show', gid)['results_current'] is True
    for fmt, endpoint in [('csv', 'csv'), ('excel', 'excel')]:
        path = Path(command('export', gid, '--format', fmt)['path'])
        assert path.parent == root / 'wblot_output' / 'exports'
        if fmt == 'csv':
            assert path.read_bytes() == client.get(f'/api/quantification_{endpoint}/{gid}').content
        else:
            from openpyxl import load_workbook
            assert load_workbook(path)['定量結果']['H2'].value == '除外'
    command('place', gid)
    assert command('show', gid)['group']['results'] is None
    assert 'error' in command('export', gid, ok=1)
    source.unlink()
    assert 'raw16' in command('start', 'TargetA', ok=1)['error']
    assert 'raw16' in command('compute', gid, ok=1)['error']
