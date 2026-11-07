import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from wb.api import import_project
from wb.geometry import Affine, Rect
from wb.propose import _exposure, _gray, _row_peaks, _row_score, _signal, propose_layout
from wb.project import load_project, locate
from wb.rendering import load_source_pixels

spec = importlib.util.spec_from_file_location('propose_fixture', Path(__file__).parent / 'fixtures/propose/generate.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def _project(archive, directory):
    import_project(archive, directory, seed_path=None)
    return load_project(locate(directory))


def _assert_format3(proposal):
    assert proposal['proposal_format'] == 3
    assert 'confidence' not in json.dumps(proposal, allow_nan=False)
    index = 0
    for group in proposal['groups']:
        positions = [np.mean(m['polygon_reference_frame'], axis=0)[::-1].tolist()
                     for m in group['membranes']]
        assert positions == sorted(positions)
        for membrane in group['membranes']:
            assert 'rows' not in membrane
            assert membrane['panel_label'] == chr(ord('A') + index)
            index += 1
            candidates = membrane['row_candidates']
            assert candidates and membrane['row'] == candidates[0]
            assert [r['score'] for r in candidates] == sorted((r['score'] for r in candidates), reverse=True)
            by_y = sorted(candidates, key=lambda r: r['band_row_y_rectified'])
            assert [r['candidate_id'] for r in by_y] == [
                f"{membrane['label']}_R{i}" for i in range(1, len(by_y) + 1)]
            for row in candidates:
                assert 'label' not in row
                crop = row['crop_rect_rectified']
                assert crop['x0'] >= membrane['sample_area']['x0_rectified']
                assert crop['x1'] <= membrane['sample_area']['x1_rectified']
                assert row['exposure_reason']
                assert row['evidence']['score_components']
            support = membrane['row']['evidence']['band_support']
            assert support['red_component_count'] or support['contains_strongest_component']


def _band_components(project, project_dir, group, membrane):
    # Count centroids of >=30-pixel components inside the membrane. All non-red
    # components darker than gray=80 in the mid exposure are kept, including edge
    # artifacts. Colorimetric handwriting is never used as chemiluminescent evidence.
    polygon = np.asarray(membrane['polygon_reference_frame'], np.float32)
    result = []
    for set_id in group['image_set_ids']:
        series = next(s for s in project.image_sets if s.image_set_id == set_id)
        captures = [next(c for c in project.captures if c.capture_id == cid) for cid in series.capture_ids]
        captures.sort(key=lambda c: (c.exposure_s or 0, c.capture_id))
        for capture, red_only in [(captures[-1], True), (captures[len(captures) // 2], False)]:
            frame = next(f for f in project.frames if f.source_asset_id == capture.presentation_source_asset_id)
            if frame.frame_id == group['reference_frame_id']:
                transform = Affine.identity(frame.frame_id)
            else:
                record = next((t for t in group['transforms'] if t['from_frame_id'] == frame.frame_id), None)
                if record is None:
                    continue
                transform = Affine.from_json(record)
            pixels = load_source_pixels(project, project_dir, frame.source_asset_id)
            red = (pixels[:, :, 0] > 240) & (pixels[:, :, 1] < 20) & (pixels[:, :, 2] < 20)
            mask = red if red_only else (cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY) < 80) & ~red
            source_poly = transform.inverse().apply(polygon)
            inside = np.zeros(mask.shape, np.uint8)
            cv2.fillPoly(inside, [np.rint(source_poly - .5).astype(np.int32)], 1)
            n, _, stats, centers = cv2.connectedComponentsWithStats((mask & (inside != 0)).astype(np.uint8), 8)
            for i in range(1, n):
                if stats[i, cv2.CC_STAT_AREA] >= 30:
                    point = transform.apply(centers[i] + .5)
                    result.append((point, red_only))
    return result


def _coverage(project, project_dir, group, membrane):
    rot = membrane['rotation']
    d = Affine.rotation_about(rot['center'], rot['degrees'], group['reference_frame_id'], 'RECT')
    result = []
    for point, red in _band_components(project, project_dir, group, membrane):
        x, y = d.apply(point)
        covered = any((r['crop_rect_rectified']['x0'] <= x <= r['crop_rect_rectified']['x1'] and
                       r['crop_rect_rectified']['y0'] <= y <= r['crop_rect_rectified']['y1'])
                      for r in membrane['row_candidates'])
        result.append((point, red, covered))
    return result


def _assert_coverage(project, project_dir, group):
    components = []
    for membrane in group['membranes']:
        covered = _coverage(project, project_dir, group, membrane)
        components.extend(covered)
        red = [point for point, is_red, _ in covered if is_red]
        if red:
            rotation = membrane['rotation']
            d = Affine.rotation_about(rotation['center'], rotation['degrees'], group['reference_frame_id'], 'RECT')
            crop = Rect(**membrane['row']['crop_rect_rectified'])
            assert any(crop.x0 <= x <= crop.x1 and crop.y0 <= y <= crop.y1 for x, y in d.apply(red))
    assert components
    assert sum(covered for _, _, covered in components) / len(components) >= .9
    assert all(covered for _, red, covered in components if red)


def _dashed_polygon(canvas, points, color):
    for a, b in zip(points, np.roll(points, -1, axis=0)):
        length = float(np.linalg.norm(b - a))
        for start in np.arange(0, length, 18):
            p0 = a + (b - a) * start / length
            p1 = a + (b - a) * min(start + 10, length) / length
            cv2.line(canvas, tuple(np.rint(p0 - .5).astype(int)), tuple(np.rint(p1 - .5).astype(int)), color, 2)


def _draw_membrane(canvas, membrane, group, ref_to_source, components):
    poly = ref_to_source.apply(membrane['polygon_reference_frame'])
    cv2.polylines(canvas, [np.rint(poly - .5).astype(np.int32)], True, (30, 190, 30), 3)
    rotation = membrane['rotation']
    d = Affine.rotation_about(rotation['center'], rotation['degrees'], group['reference_frame_id'], 'RECT')
    # Draw the selected crop last so an overlapping alternative cannot hide it.
    chosen = membrane['row']['candidate_id']
    rows = sorted(membrane['row_candidates'], key=lambda r: r['candidate_id'] == chosen)
    for row in rows:
        points = ref_to_source.apply(d.inverse().apply(Rect(**row['crop_rect_rectified']).corners()))
        selected = row['candidate_id'] == chosen
        color = (230, 100, 0) if selected else (170, 80, 170)
        if selected:
            cv2.polylines(canvas, [np.rint(points - .5).astype(np.int32)], True, color, 3)
        else:
            _dashed_polygon(canvas, points, color)
        label = f"{row['candidate_id']} {row['score']:.2f}"
        if selected:
            label = f"{membrane['panel_label']} {label} {row['selected_exposure_id']}"
        pos = tuple(np.rint(points[0] + [4, 20]).astype(int))
        cv2.putText(canvas, label, pos, cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 4)
        cv2.putText(canvas, label, pos, cv2.FONT_HERSHEY_SIMPLEX, .55, color, 2)
    for point, red, covered in components:
        pos = tuple(np.rint(ref_to_source.apply(point) - .5).astype(int))
        cv2.circle(canvas, pos, 5, (0, 220, 220) if covered else (255, 0, 255), -1)
        if red:
            cv2.circle(canvas, pos, 7, (0, 100, 180), 1)


def _shots(project, project_dir, proposal, prefix):
    out = Path('/tmp/wb-f3/shots')
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for group in proposal['groups']:
        marker_to_ref = Affine.from_json(group['transforms'][0])
        views = [(group['marker_frame_id'], marker_to_ref.inverse())]
        for set_id in group['image_set_ids']:
            series = next(s for s in project.image_sets if s.image_set_id == set_id)
            captures = [next(c for c in project.captures if c.capture_id == cid) for cid in series.capture_ids]
            captures.sort(key=lambda c: (c.exposure_s or 0, c.capture_id), reverse=True)
            for capture in captures:
                frame = next(f for f in project.frames if f.source_asset_id == capture.presentation_source_asset_id)
                transform = next((t for t in group['transforms'] if t['from_frame_id'] == frame.frame_id), None)
                if frame.frame_id == group['reference_frame_id']:
                    views.append((frame.frame_id, Affine.identity(frame.frame_id)))
                    break
                if transform:
                    views.append((frame.frame_id, Affine.from_json(transform).inverse()))
                    break
        components = [_coverage(project, project_dir, group, membrane) for membrane in group['membranes']]
        for frame_id, ref_to_source in views:
            frame = next(f for f in project.frames if f.frame_id == frame_id)
            canvas = cv2.cvtColor(load_source_pixels(project, project_dir, frame.source_asset_id), cv2.COLOR_RGB2BGR)
            for membrane, dots in zip(group['membranes'], components):
                _draw_membrane(canvas, membrane, group, ref_to_source, dots)
            cv2.putText(canvas, 'Solid blue: selected | dashed purple: candidates | yellow: covered | magenta: missed',
                        (20, 30), cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 0, 0), 2)
            path = out / f'{prefix}-{frame_id}.png'
            assert cv2.imwrite(str(path), canvas)
            paths.append(str(path))
    return paths


def test_real_layout(input_zip, tmp_path):
    project = _project(input_zip, tmp_path)
    proposal = propose_layout(project, tmp_path)
    _assert_format3(proposal)
    assert json.dumps(proposal, sort_keys=True) == json.dumps(propose_layout(project, tmp_path), sort_keys=True)
    assert proposal['proposal_format'] == 3
    assert len(proposal['groups']) == 1
    group = proposal['groups'][0]
    assert group['marker_frame_id'] == 'FRAME_IMG050'
    assert group['image_set_ids'] == ['S04', 'S05']
    assert {e['image_set_id'] for e in proposal['excluded_image_sets']} == {'S01', 'S02', 'S03'}
    assert all(e['reason'] for e in proposal['excluded_image_sets'])
    assert len(group['membranes']) == 1
    membrane = group['membranes'][0]
    assert membrane['label'] == 'M1'
    expected = np.array([[515, 317], [1562, 288], [1579, 1002], [510, 978]])
    assert np.max(np.linalg.norm(np.array(membrane['polygon_marker_frame']) - expected, axis=1)) < 8
    assert membrane['rotation']['degrees'] == pytest.approx(1.502, abs=.05)
    assert membrane['marker_lanes'] is not None
    assert len(membrane['row_candidates']) >= 2
    assert membrane['panel_label'] == 'A'
    assert all(max(abs(t['matrix'][0][2]), abs(t['matrix'][1][2])) < 20 for t in group['transforms'])
    assert all(t['status'] == 'candidate' for t in group['transforms'])
    _shots(project, tmp_path, proposal, 'real')
    _assert_coverage(project, tmp_path, group)


def _synthetic(tmp_path, **kwargs):
    archive = tmp_path / 'input.zip'
    generator.make_zip(archive, **kwargs)
    directory = tmp_path / 'project'
    project = _project(archive, directory)
    proposal = propose_layout(project, directory)
    _assert_format3(proposal)
    return project, directory, proposal


def test_two_membranes_and_stray(tmp_path):
    project, directory, proposal = _synthetic(tmp_path, stray=True)
    assert len(proposal['groups']) == 1
    group = proposal['groups'][0]
    assert len(proposal['excluded_image_sets']) == 1
    assert proposal['excluded_image_sets'][0]['reason']
    assert [m['label'] for m in group['membranes']] == ['M1', 'M2']
    for membrane, degrees in zip(group['membranes'], [1, -2]):
        assert membrane['rotation']['degrees'] == pytest.approx(degrees, abs=.3)
        assert len(membrane['row_candidates']) == 2
    assert [m['panel_label'] for m in group['membranes']] == ['A', 'B']
    _shots(project, directory, proposal, 'synthetic')


def test_two_sessions(tmp_path):
    _, _, proposal = _synthetic(tmp_path, sessions=2, two=False)
    assert len(proposal['groups']) == 2
    assert all(len(group['image_set_ids']) == 1 for group in proposal['groups'])
    assert not proposal['excluded_image_sets']
    assert not proposal['unmatched_marker_frames']


def test_no_marker(tmp_path):
    _, _, proposal = _synthetic(tmp_path, markers=False)
    assert proposal['groups']
    assert all(group['marker_frame_id'] is None for group in proposal['groups'])
    assert all(m['marker_lanes'] is None for group in proposal['groups'] for m in group['membranes'])
    assert 'colorimetric' in proposal['warnings'][0].lower()


def test_missing_ladder(tmp_path):
    _, _, proposal = _synthetic(tmp_path, two=False, ladder=False)
    group = proposal['groups'][0]
    assert group['membranes'][0]['marker_lanes'] is None
    assert any('no visible ladder' in warning for warning in group['warnings'])


def test_second_real_layout(tmp_path):
    archive = Path(os.environ.get('WB_INPUT_ZIP_2',
        Path(__file__).resolve().parents[1] / 'private' / 'input_2.zip'))
    if not archive.exists():
        pytest.skip('second real dataset is unavailable')
    project = _project(archive, tmp_path)
    proposal = propose_layout(project, tmp_path)
    _assert_format3(proposal)
    assert proposal == propose_layout(project, tmp_path)
    assert len(proposal['groups']) == 1
    group = proposal['groups'][0]
    assert group['image_set_ids'] == ['S01', 'S02']
    assert all(max(abs(t['matrix'][0][2]), abs(t['matrix'][1][2])) < 20 for t in group['transforms'])
    expected = [(210, 310, 760, 590), (820, 295, 1350, 875), (180, 640, 755, 945),
                (1465, 270, 1715, 1365), (455, 1080, 1270, 1335)]
    assert len(group['membranes']) == len(expected)
    for membrane, box in zip(group['membranes'], expected):
        poly = np.asarray(membrane['polygon_marker_frame'])
        actual = [*poly.min(axis=0), *poly.max(axis=0)]
        assert np.max(np.abs(np.array(actual) - box)) < 30
        angle = membrane['rotation']['degrees']
        assert abs(abs(angle) - 90) < 3 if box[0] == 1465 else abs(angle) < 6
        assert membrane['row_candidates']
        rotation = membrane['rotation']
        d = Affine.rotation_about(rotation['center'], angle, group['reference_frame_id'], 'RECT')
        polygon = np.asarray(membrane['polygon_reference_frame'], np.float32)
        frame = next(f for f in project.frames if f.frame_id == group['reference_frame_id'])
        for row in membrane['row_candidates']:
            corners = d.inverse().apply(Rect(**row['crop_rect_rectified']).corners())
            assert all(cv2.pointPolygonTest(polygon, tuple(map(float, p)), False) >= 0 for p in corners)
            assert np.all(corners >= 0) and np.all(corners <= [frame.width, frame.height])
    assert [m['panel_label'] for m in group['membranes']] == ['A', 'B', 'C', 'D', 'E']
    strip = np.asarray(group['membranes'][3]['polygon_marker_frame'], np.float32)
    for point in [(1480, 285), (1700, 285), (1700, 1350), (1480, 1350)]:
        assert cv2.pointPolygonTest(strip, point, False) >= 0
    assert any('ambiguous by 180' in warning for warning in group['warnings'])
    _shots(project, tmp_path, proposal, 'real2')
    _assert_coverage(project, tmp_path, group)


def test_red_is_signal_but_is_masked_for_registration():
    pixels = np.full((60, 60, 3), 255, np.uint8)
    pixels[10:50, 10:50] = [255, 0, 0]
    assert _signal(pixels)[30, 30] == 1
    assert _gray(pixels)[30, 30] == 0


def test_color_membrane_near_left_edge_can_rotate_through_negative_coordinates(tmp_path):
    import tifffile
    from wb import api
    from wb.rendering import render_panel

    pixels = np.full((700, 500, 3), 45, np.uint8)
    cv2.rectangle(pixels, (30, 100), (150, 650), (225, 225, 225), -1)
    for y in range(180, 610, 80):
        cv2.ellipse(pixels, (95, y), (6, 16), 0, 0, 360, (35, 35, 35), -1)
    for modality in ('Colorimetric', 'Chemiluminescence'):
        tifffile.imwrite(tmp_path / f'60.000s({modality}).tif', pixels, photometric='rgb')
    project = api.import_project(tmp_path)
    proposal = api.propose(tmp_path)
    membrane = proposal['groups'][0]['membranes'][0]
    assert abs(abs(membrane['rotation']['degrees']) - 90) < 1
    assert membrane['row']['crop_rect_rectified']['x0'] < 0
    saved = api.apply_proposal(tmp_path, proposal, project.project_revision)
    assert render_panel(saved, tmp_path, saved.panels[0].panel_id,
                        saved.panels[0].selected_exposure_id).png
    assert api.validate(tmp_path)['source_issues'] == []


def test_row_score_prefers_repeated_strong_lanes_to_smear_and_faint_rows():
    signal = generator.scoring_signal()
    scores = []
    for y in (90, 215, 315):
        score, components = _row_score(signal, Rect(50, y - 40, 650, y + 40), y, .05)
        scores.append(score)
        expected = (2 * components['lane_structure'] + 2 * components['strength_p90']
                    + 3 * components['profile_prominence'] - components['vertical_continuity'])
        assert score == pytest.approx(expected)
    assert scores[0] > scores[1] and scores[0] > scores[2]


def test_nearby_profile_peaks_merge_but_separate_rows_remain():
    ys = np.arange(300)
    profile = sum(np.exp(-((ys - y) / 4) ** 2) * height for y, height in [(80, 1), (105, .8), (210, .5)])
    peaks, _ = _row_peaks(profile.astype(np.float32), 10, 290)
    assert len(peaks) == 2
    assert peaks[0][0] == pytest.approx(80.5, abs=1)
    assert peaks[1][0] == pytest.approx(210.5, abs=1)
    assert all(prominence > 0 for _, prominence in peaks)


def test_exposure_choice_counts_red_in_each_sources_own_frame():
    frame = SimpleNamespace(width=150, height=100)
    short = SimpleNamespace(capture_id='short', exposure_s=10)
    long = SimpleNamespace(capture_id='long', exposure_s=100)
    images = {name: np.full((100, 150, 3), 255, np.uint8) for name in ('short', 'long')}
    images['long'][20:30, 63:73] = [255, 0, 0]
    registered = [(short, frame, Affine.identity('ref')),
                  (long, frame, Affine.translation(7, 0, 'source', 'ref'))]
    d = Affine.identity('ref', 'rect')
    image = lambda capture: images[capture.capture_id]
    chosen, _, counts = _exposure(Rect(70, 20, 80, 30), d, registered, image)
    assert chosen == 'short' and counts == {'short': 0, 'long': 100}
    chosen, reason, counts = _exposure(Rect(100, 20, 110, 30), d, registered, image)
    assert chosen == 'long' and not any(counts.values())
    assert 'longest' in reason
    images['short'][20:22, 70:75] = [255, 0, 0]
    chosen, reason, counts = _exposure(Rect(70, 20, 80, 30), d, registered, image)
    assert chosen == 'short' and 0 < counts['short'] < counts['long']
    assert 'fallback' in reason and 'fewest' in reason


def test_narrow_membrane_horizontal_bands_keep_faint_outer_lane():
    from wb.geometry import warp
    from wb.propose import _membrane
    frame = SimpleNamespace(frame_id='ref', width=500, height=750)
    capture = SimpleNamespace(capture_id='short', exposure_s=60)
    polygon = Rect(120, 50, 300, 700).corners()
    image = np.full((750, 500, 3), 45, np.uint8)
    cv2.rectangle(image, (120, 50), (300, 700), (230, 230, 230), -1)
    for x, gray in [(150, 30), (210, 30), (270, 226)]:
        cv2.ellipse(image, (x, 375), (20, 6), 0, 0, 360, (gray,) * 3, -1)
    turn = Affine.rotation_about((210, 375), 10, 'base', 'ref')
    image = warp(image, turn, 500, 750)
    polygon = turn.apply(polygon)
    membrane = _membrane(polygon, 'M1', 'rect', Affine.identity('ref'), frame,
        image, image, [(capture, frame, Affine.identity('ref'))], lambda _: image, [], {'short'})
    assert membrane['rotation']['degrees'] == pytest.approx(-10, abs=1)
    d = Affine.rotation_about(membrane['rotation']['center'], membrane['rotation']['degrees'], 'ref', 'rect')
    crop = Rect(**membrane['row']['crop_rect_rectified'])
    # The third lane is nearly negative; preserve its physical width nonetheless.
    for center in turn.apply([[150, 375], [210, 375], [270, 375]]):
        x, y = d.apply(center)
        assert crop.x0 + 15 < x < crop.x1 - 15
        assert crop.y0 < y < crop.y1


def test_outline_registration_ignores_membranes_without_supported_edges():
    from wb.propose import _register_outlines
    image = np.full((650, 650, 3), 255, np.uint8)
    visible = Rect(40, 60, 240, 230).corners()
    unrelated = Rect(320, 320, 590, 580).corners()
    cv2.polylines(image, [np.rint(visible + [4, 6]).astype(np.int32)], True, (30,) * 3, 2)
    # A distant edge is attractive only at the search boundary and is not a
    # supported observation of this colorimetric membrane.
    cv2.polylines(image, [np.rint(unrelated + [0, 26]).astype(np.int32)], True, (30,) * 3, 2)
    transform, _ = _register_outlines(image, [visible, unrelated], 'source', 'target')
    assert transform.matrix[:2, 2] == pytest.approx([-4, -6], abs=3)


def test_detected_band_is_not_clipped_by_inscribed_membrane_crop():
    from wb.propose import _row_candidates
    signal = np.zeros((250, 500), np.float32)
    for x in (120, 190, 260):
        cv2.ellipse(signal, (x, 178), (20, 10), 0, 0, 360, 0.8, -1)
    polygon = np.array([[50, 50], [450, 50], [450, 175], [50, 200]], float)
    frame = SimpleNamespace(frame_id='ref', width=500, height=250)
    capture = SimpleNamespace(capture_id='short', exposure_s=60)
    image = np.full((250, 500, 3), 255, np.uint8)
    rows = _row_candidates(signal, polygon, 51, 449, Affine.identity('ref'), frame,
        [(capture, frame, Affine.identity('ref'))], lambda _: image, 'M1', [])
    assert rows
    row = rows[0]
    assert row['crop_rect_rectified']['y1'] >= row['evidence']['band_span_y_rectified'][1] + 12
    assert row['evidence']['expanded_past_estimated_membrane_edge']


def test_narrow_membrane_without_directional_signal_does_not_force_quarter_turn():
    from wb.propose import _orientation
    polygon = Rect(30, 20, 150, 650).corners()
    image = np.full((700, 300, 3), 255, np.uint8)
    degrees, _, evidence = _orientation(polygon, image, np.zeros((700, 300), np.float32), 'M1', [])
    assert degrees == 0
    assert evidence['band_direction_degrees'] is None
