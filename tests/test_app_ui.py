"""The committed Svelte build against the real local HTTP API, with synthetic sources."""
from contextlib import contextmanager
import copy
import json
import re
from pathlib import Path
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from wb import api, project as P
from wb.app import OPERATIONS, create_app
from wb.geometry import Affine, Rect, polygon_within_frame
from test_rendering import small_project

DIST = Path(__file__).resolve().parents[1] / 'src/wb/web/app/dist'
SHOTS = Path('/tmp/wb-svelte/shots')


@pytest.fixture
def app_project(small_project, monkeypatch):
    path, initial = small_project
    initial.panels = []
    initial.membrane_regions = []
    initial.image_sets[0].capture_ids = ['C1', 'C2']
    initial.captures[2].modality = 'colorimetric'
    for n, capture in enumerate(initial.captures, 1):
        capture.exposure_s = n * 10
    initial = P.save_project(initial, P.locate(path), initial.project_revision, 'test_ui')
    proposal = json.loads((Path(__file__).parent / 'fixtures/apply/proposal.json').read_text())
    group = proposal['groups'][0]
    group.update(marker_frame_id='F3', reference_frame_id='F1', image_set_ids=['SET'])
    for transform in group['transforms']:
        transform['from_frame_id'] = 'F3' if transform['kind'] == 'marker_to_reference' else 'F2'
        transform['to_frame_id'] = 'F1'
    for membrane in group['membranes']:
        for name in ('polygon_marker_frame', 'polygon_reference_frame'):
            membrane[name] = [[x / 40, y / 30] for x, y in membrane[name]]
        membrane['rotation']['center'] = [1030 / 40, 650 / 30]
        for area_name in ('marker_lanes', 'sample_area'):
            if membrane[area_name]:
                for key in ('x0_rectified', 'x1_rectified'):
                    membrane[area_name][key] /= 40
        membrane['lane_candidates']['centers_x_rectified'] = [17.5, 20, 22.5]
        # row is a separate copy in the fixture; both must stay exactly equal.
        for row in [membrane['row'], *membrane['row_candidates']]:
            for key in ('x0', 'x1'):
                row['crop_rect_rectified'][key] /= 40
            for key in ('y0', 'y1'):
                row['crop_rect_rectified'][key] /= 30
            row['band_row_y_rectified'] /= 30
            row['selected_exposure_id'] = 'C2' if row['selected_exposure_id'] == 'CAP_IMG031' else 'C1'
    monkeypatch.setattr(api, 'propose', lambda project_dir: copy.deepcopy(proposal))
    return path, initial, proposal


@contextmanager
def running_svelte(path):
    if not (DIST / 'index.html').is_file():
        pytest.skip('Svelte dist is missing; run npm install && npm run build in src/wb/web/app')
    app = create_app(path)
    required = {'/api/revision', '/api/status', '/api/layout', '/api/export'}
    missing = sorted(required - {route.path for route in app.routes})
    if missing:
        pytest.skip('Concurrent backend endpoints not available: ' + ', '.join(missing))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    try:
        assert server.started, 'FastAPI did not start within 10 seconds'
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@contextmanager
def browser_page(path):
    with running_svelte(path) as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=2)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            expect(page.locator('.workbench')).to_be_visible(timeout=10000)
            yield page, context.request, url
            assert not errors, errors
        finally:
            if errors:
                print('Browser errors:', errors)
            context.close()
            browser.close()


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_function("Array.from(document.querySelectorAll('img')).filter(i => i.getAttribute('src') && i.getBoundingClientRect().top < innerHeight).every(i => i.complete && i.naturalWidth > 0)")
    page.screenshot(path=str(SHOTS / name))


def applied(app_project):
    path, initial, proposal = app_project
    return path, api.apply_proposal(path, proposal, initial.project_revision)


def need(*operations):
    missing = [name for name in operations if name not in OPERATIONS]
    if missing:
        pytest.skip('Backend operations not available yet: ' + ', '.join(missing))


def view_box(page):
    return list(map(float, page.locator('svg.canvas').get_attribute('viewBox').split()))


def client_point(page, x, y):
    return page.evaluate("""([x, y]) => {
        const svg = document.querySelector('svg.canvas'), point = svg.createSVGPoint();
        point.x = x; point.y = y;
        const mapped = point.matrixTransform(svg.getScreenCTM());
        return [mapped.x, mapped.y];
    }""", [x, y])


def centre(locator):
    box = locator.bounding_box()
    return [box['x'] + box['width'] / 2, box['y'] + box['height'] / 2]


def drag(page, start, end, steps=8):
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(*end, steps=steps)
    page.mouse.up()


def corners_inside_frame(project, panel):
    dg = panel.display_geometry
    transform = next(t for t in project.transforms if t.transform_id == dg.reference_to_rectified_transform_id)
    frame = next(f for f in project.frames if f.frame_id == dg.reference_frame_id)
    points = Affine.from_json(transform.model_dump()).inverse().apply(Rect(**dg.crop_rect.model_dump()).corners())
    return polygon_within_frame(points, frame.width, frame.height)


def rotation_degrees(project, panel):
    import math
    matrix = next(t for t in project.transforms if t.transform_id == panel.display_geometry.reference_to_rectified_transform_id).matrix
    return math.degrees(math.atan2(matrix[1][0], matrix[0][0]))


def row(page, index):
    return page.locator('.row').nth(index)


def band_stage(page):
    tab = page.get_by_role('tab', name='2. バンド選択', exact=True)
    tab.click()
    expect(tab).to_have_attribute('aria-selected', 'true')


def activate_region(page, region_id):
    page.get_by_role('tab', name='1. メンブレン選択', exact=True).click()
    page.locator(f'.membrane-item[data-region="{region_id}"]').click()
    band_stage(page)


def test_one_screen_workbench_has_no_wizard_forms_or_legacy_link(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        expect(page.locator('.row')).to_have_count(2)
        expect(page.locator('.membrane-id')).to_have_text(['M1', 'M2'])
        for locator in (page.locator('.figure'), page.locator('svg.canvas'), page.locator('.exposure-strip')):
            expect(locator).to_be_visible()
            box = locator.bounding_box()
            assert box['y'] >= 0 and box['y'] + box['height'] <= 1000 + 1 and box['width'] > 100
        assert page.locator('a[href*="legacy"]').count() == 0
        assert page.locator('input[type=number]').count() == 0
        assert page.locator('input[type=range]').count() == 0
        for label in ('①提案の確認', '②段ごとの調整', '③書き出し', '根拠', 'ラベルを保存'):
            assert label not in page.inner_text('body')
        classes = page.locator('input').evaluate_all('nodes => nodes.map(n => n.classList[0])')
        assert set(classes) <= {'inline-label', 'lane-name', 'include-check'}
        assert page.evaluate('document.documentElement.scrollHeight') <= 1001
        shot(page, '01-workbench.png')


def test_empty_project_has_no_auto_proposal_button(app_project):
    path, initial, proposal = app_project
    with browser_page(path) as (page, client, url):
        expect(page.get_by_role('button', name='マーカー', exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('svg.canvas')).to_have_attribute('data-fit', 'frame')
        expect(page.locator('svg.canvas image')).to_have_attribute('href', re.compile('frame/F3'))
        expect(page.get_by_role('button', name='Merge', exact=True)).to_be_disabled()
        expect(page.get_by_role('button', name=re.compile('自動提案'))).to_have_count(0)
        expect(page.get_by_role('button', name='メンブレンを追加', exact=True)).to_be_visible()
        expect(page.locator('.row')).to_have_count(0)
        assert client.post(url + '/api/propose', data={}).status == 404


def test_color_preview_is_available_without_a_registration(app_project):
    path, project = applied(app_project)
    project.transforms = [t for t in project.transforms if t.kind != 'marker_to_reference']
    project.marker_registrations = []
    P.save_project(project, P.locate(path), project.project_revision, 'remove_test_registration')
    with browser_page(path) as (page, client, url):
        expect(page.get_by_role('button', name='マーカー', exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('svg.canvas image')).to_have_attribute('href', re.compile('frame/F3'))
        expect(page.locator('polygon.membrane')).to_have_count(0)
        expect(page.get_by_role('button', name='Merge', exact=True)).to_be_disabled()
        page.get_by_role('button', name='メンブレンを追加', exact=True).click()
        expect(page.locator('svg.canvas')).to_have_attribute('data-mode', 'lum')
        expect(page.locator('svg.canvas')).to_have_attribute('data-draw', 'membrane')
        expect(page.locator('polygon.membrane')).to_have_count(2)


def test_selection_is_bidirectional_between_figure_and_canvas(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        first, second = project.panels
        expect(page.locator('.membrane-item.active')).to_have_attribute('data-region', first.region_id)
        expect(page.locator('.pill')).to_contain_text('M1')
        before = api._load(path).model_dump(mode='json')
        page.locator(f'polygon.membrane[data-region="{second.region_id}"]').click(position={'x': 4, 'y': 4})
        expect(page.locator('.membrane-item.active')).to_have_attribute('data-region', second.region_id)
        expect(page.locator('.pill')).to_contain_text('M2')
        expect(page.locator('polygon.membrane.selected')).to_have_attribute('data-region', second.region_id)
        band_stage(page)
        expect(page.locator('.row.selected')).to_have_attribute('data-panel', second.panel_id)
        # The band step also allows direct membrane selection from an image.
        row(page, 0).locator('.visual').click()
        expect(page.locator('.row.selected')).to_have_attribute('data-panel', first.panel_id)
        expect(page.locator('.pill')).to_contain_text('M1')
        assert api._load(path).model_dump(mode='json') == before




def test_dragging_crop_handles_and_rotation_saves_float_geometry(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        before = api._load(path)
        old = before.panels[0]
        top = page.locator('circle[data-handle="top"]')
        start = centre(top)
        drag(page, start, [start[0], start[1] - 14])
        expect(page.get_by_text('編集を保存しました', exact=True)).to_be_visible()
        after = api._load(path)
        crop, old_crop = after.panels[0].display_geometry.crop_rect, old.display_geometry.crop_rect
        assert after.project_revision == before.project_revision + 1
        assert after.history[-1].operation == 'save_panel_edit'
        assert 0 < old_crop.y0 - crop.y0 < 2 and not float(crop.y0).is_integer()
        assert (crop.y1, crop.x0, crop.x1) == (old_crop.y1, old_crop.x0, old_crop.x1)
        assert after.panels[0].display_geometry.geometry_revision == old.display_geometry.geometry_revision + 1
        bottom = page.locator('circle[data-handle="bottom"]')
        start = centre(bottom)
        drag(page, start, [start[0], start[1] + 12])
        expect(page.get_by_text('編集を保存しました', exact=True)).to_be_visible()
        final = api._load(path).panels[0].display_geometry.crop_rect
        assert 0 < final.y1 - crop.y1 < 2 and final.y0 == crop.y0
        angle_before = rotation_degrees(api._load(path), api._load(path).panels[0])
        rotate = page.locator('circle[data-handle="rotate"]')
        start = centre(rotate)
        revision = api._load(path).project_revision
        drag(page, start, [start[0], start[1] + 40])
        expect(page.locator('.pill b')).not_to_have_text(f'{round(angle_before, 1):g}°')
        # The label changes while dragging; wait for the save itself before reading the file.
        for _ in range(100):
            current = api._load(path)
            if current.project_revision > revision:
                break
            page.wait_for_timeout(50)
        assert current.project_revision == revision + 1
        angle_after = rotation_degrees(current, current.panels[0])
        assert abs(angle_after - angle_before) > .3
        transform = next(t for t in current.transforms if t.transform_id == current.panels[0].display_geometry.reference_to_rectified_transform_id)
        assert transform.evidence['center'] == pytest.approx([1030 / 40, 650 / 30])
        assert corners_inside_frame(current, current.panels[0])
        reloaded = float(page.locator('.pill b').inner_text().rstrip('°'))
        assert reloaded == pytest.approx(angle_after, abs=.06)
        shot(page, '02-edited.png')


def test_handle_drags_are_clamped_inside_the_frame(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        page.get_by_role('button', name='全体', exact=True).click()
        expect(page.locator('svg.canvas')).to_have_attribute('data-fit', 'frame')
        top = page.locator('circle[data-handle="top"]')
        start = centre(top)
        outside = client_point(page, 25, -4)
        drag(page, start, [start[0], outside[1]], steps=12)
        expect(page.get_by_text('編集を保存しました', exact=True)).to_be_visible()
        current = api._load(path)
        assert corners_inside_frame(current, current.panels[0])
        assert current.panels[0].display_geometry.crop_rect.y0 < project.panels[0].display_geometry.crop_rect.y0
        bottom = page.locator('circle[data-handle="bottom"]')
        start = centre(bottom)
        outside = client_point(page, 25, 44)
        drag(page, start, [start[0], outside[1]], steps=12)
        expect(page.get_by_text('編集を保存しました', exact=True).first).to_be_visible()
        page.wait_for_function('() => document.querySelector(".toast")')
        current = api._load(path)
        assert corners_inside_frame(current, current.panels[0])
        # Crossing the handles over each other never inverts the band.
        start = centre(page.locator('circle[data-handle="top"]'))
        low = client_point(page, 25, 30)
        drag(page, start, [start[0], low[1]], steps=12)
        current = api._load(path)
        crop = current.panels[0].display_geometry.crop_rect
        assert crop.y1 - crop.y0 >= min(6, project.panels[0].display_geometry.crop_rect.y1 - project.panels[0].display_geometry.crop_rect.y0) - 1e-9
        start = centre(page.locator('circle[data-handle="rotate"]'))
        far = client_point(page, 49, 39)
        drag(page, start, [start[0], far[1]], steps=12)
        current = api._load(path)
        assert corners_inside_frame(current, current.panels[0])


def test_zoom_pan_and_fit_never_change_stored_geometry(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        canvas = page.locator('svg.canvas')
        before = api._load(path).model_dump(mode='json')
        base = view_box(page)
        points = page.locator('polygon.chosen').get_attribute('points')
        box = canvas.bounding_box()
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.wheel(0, -600)
        page.wait_for_function('() => Number(document.querySelector("svg.canvas").dataset.zoom) > 1.5')
        zoomed = view_box(page)
        assert zoomed[2] < base[2] and zoomed[2] >= base[2] / 6 - 1e-6
        for _ in range(6):
            page.mouse.wheel(0, -900)
        page.wait_for_function('() => Number(document.querySelector("svg.canvas").dataset.zoom) > 5.9')
        assert float(canvas.get_attribute('data-zoom')) <= 6.0001
        page.mouse.wheel(0, 3000)
        page.wait_for_function('() => document.querySelector("svg.canvas").dataset.zoom === "1.000"')
        assert view_box(page) == pytest.approx(base)
        page.mouse.wheel(0, -500)
        page.wait_for_function('() => Number(document.querySelector("svg.canvas").dataset.zoom) > 1.5')
        zoomed = view_box(page)
        drag(page, [box['x'] + 12, box['y'] + 12], [box['x'] + 80, box['y'] + 55])
        panned = view_box(page)
        assert panned[0] < zoomed[0] and panned[1] < zoomed[1] and panned[2] == zoomed[2]
        assert page.locator('polygon.chosen').get_attribute('points') == points
        page.get_by_role('button', name='全体', exact=True).click()
        expect(canvas).to_have_attribute('viewBox', '0 0 50 40')
        expect(canvas).to_have_attribute('data-fit', 'frame')
        page.get_by_role('button', name='膜', exact=True).click()
        expect(canvas).to_have_attribute('data-fit', 'membrane')
        assert view_box(page) == pytest.approx(base)
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.wheel(0, -500)
        page.wait_for_function('() => Number(document.querySelector("svg.canvas").dataset.zoom) > 1.5')
        canvas.dblclick(position={'x': 10, 'y': 10})
        expect(canvas).to_have_attribute('data-zoom', '1.000')
        assert page.locator('polygon.chosen').get_attribute('points') == points
        assert api._load(path).model_dump(mode='json') == before


def test_inline_label_saves_on_enter_and_blur_without_locking_selection(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        first, second = page.locator('.inline-label').nth(0), page.locator('.inline-label').nth(1)
        first.fill('TargetA')
        first.press('Enter')
        expect(page.get_by_text('ラベルを変更しました', exact=True)).to_be_visible()
        assert api._load(path).panels[0].label == 'TargetA'
        revision = api._load(path).project_revision
        first.focus()
        first.press('Enter')
        page.wait_for_timeout(300)
        assert api._load(path).project_revision == revision
        first.fill('TargetD')
        row(page, 1).locator('.visual').click()
        expect(page.locator('.row.selected')).to_have_attribute('data-panel', project.panels[0].panel_id)
        for _ in range(40):
            if api._load(path).panels[0].label == 'TargetD':
                break
            page.wait_for_timeout(50)
        assert api._load(path).panels[0].label == 'TargetD'
        # The second row belongs to another membrane; make it active via the first step.
        activate_region(page, project.panels[1].region_id)
        second = page.locator('.inline-label').nth(1)
        second.fill('')
        second.press('Enter')
        expect(second).to_have_value(api._load(path).panels[1].label)
        second.fill('Actin')
        second.press('Escape')
        expect(second).to_have_value(api._load(path).panels[1].label)
        assert api._load(path).panels[1].label != 'Actin'


def test_poll_reads_external_change_but_waits_while_typing(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        response = client.post(url + '/api/rename_panel', data={
            'panel_id': project.panels[0].panel_id, 'label': 'TargetA', 'expected_revision': project.project_revision,
        })
        assert response.ok, response.text()
        expect(page.get_by_text('外部の変更を読み込みました', exact=True)).to_be_visible(timeout=8000)
        expect(page.locator('.inline-label').first).to_have_value('TargetA')
        revision = api._load(path).project_revision
        activate_region(page, project.panels[1].region_id)
        second = page.locator('.inline-label').nth(1)
        second.click()
        second.fill('入力中')
        response = client.post(url + '/api/rename_panel', data={
            'panel_id': project.panels[0].panel_id, 'label': 'TargetE', 'expected_revision': revision,
        })
        assert response.ok
        page.wait_for_timeout(3000)
        expect(second).to_have_value('入力中')
        expect(page.locator('.inline-label').first).to_have_value('TargetA')
        page.get_by_role('button', name='PNG', exact=True).focus()
        expect(page.locator('.inline-label').first).to_have_value('TargetE', timeout=8000)


def test_conflict_and_server_rejection_are_reported(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        page.route('**/api/revision', lambda route: route.fulfill(json={'project_revision': project.project_revision}))
        response = client.post(url + '/api/rename_panel', data={
            'panel_id': project.panels[0].panel_id, 'label': '外部', 'expected_revision': project.project_revision,
        })
        assert response.ok
        first = page.locator('.inline-label').first
        first.fill('ローカル')
        first.press('Enter')
        expect(page.get_by_role('alert')).to_contain_text('外部で変更されています')
        expect(first).to_have_value('外部')
        assert api._load(path).panels[0].label == '外部'
        page.route('**/api/duplicate_panel', lambda route: route.fulfill(status=422, json={'detail': '入力を確認してください: テスト'}))
        revision = api._load(path).project_revision
        row(page,0).get_by_role('button',name='Selectionを複製',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('入力を確認してください')
        assert api._load(path).project_revision == revision


def test_exposure_strip_scrolls_to_adopted_and_separates_preview_from_adopt(app_project):
    path, project = applied(app_project)
    for index in range(4, 16):
        capture = project.captures[1].model_copy(deep=True)
        capture.capture_id = f'C{index}'
        capture.exposure_s = index * 10
        project.captures.append(capture)
        project.image_sets[0].capture_ids.append(capture.capture_id)
    project.panels[0].selected_exposure_id = 'C14'
    project.panels[1].selected_exposure_id = 'C15'
    project = P.save_project(project, P.locate(path), project.project_revision, 'test_exposure_scroll')
    with browser_page(path) as (page, client, url):
        band_stage(page)
        for index, capture_id in [(0, 'C14'), (1, 'C15'), (0, 'C14')]:
            activate_region(page, project.panels[index].region_id)
            row(page, index).locator('.visual').click()
            adopted = page.locator(f'.exposure-strip [data-capture="{capture_id}"]')
            expect(page.locator('.exposure-strip article.adopted')).to_have_attribute('data-capture', capture_id)
            page.wait_for_function('''id => {
                const strip = document.querySelector('.exposure-strip').getBoundingClientRect();
                const item = document.querySelector(`.exposure-strip [data-capture="${id}"]`).getBoundingClientRect();
                return item.left >= strip.left && item.right <= strip.right;
            }''', arg=capture_id)
            assert adopted.evaluate("node => node.classList.contains('previewing')")
        card = page.locator('.exposure-strip [data-capture="C2"]')
        expect(card).to_contain_text('20s')
        revision = api._load(path).project_revision
        page.get_by_role('button', name='露光 C2 を比較', exact=True).click()
        expect(card).to_have_class(re.compile('previewing'))
        expect(page.locator('.row.selected .compare')).to_have_text('比較')
        expect(page.locator('.row.selected .blot img')).to_have_attribute('data-capture', 'C2')
        expect(row(page, 1).locator('.compare')).to_have_count(0)
        expect(row(page, 1).locator('.blot img')).to_have_attribute('data-capture', 'C15')
        assert api._load(path).project_revision == revision
        assert api._load(path).panels[0].selected_exposure_id == 'C14'
        card.get_by_role('button', name='露光 C2 を採用').click()
        expect(page.get_by_text('採用露光を変更しました', exact=True)).to_be_visible()
        assert api._load(path).panels[0].selected_exposure_id == 'C2'
        expect(page.locator('.row.selected .compare')).to_have_count(0)
        page.locator('.exposure-strip [data-capture="C5"] .thumb').dblclick()
        expect(page.get_by_text('採用露光を変更しました', exact=True)).to_be_visible()
        for _ in range(40):
            if api._load(path).panels[0].selected_exposure_id == 'C5':
                break
            page.wait_for_timeout(50)
        assert api._load(path).panels[0].selected_exposure_id == 'C5'
        # Previewing another exposure shows that exposure's frame on the canvas.
        page.get_by_role('button', name='露光 C14 を比較', exact=True).click()
        expect(page.locator('svg.canvas image').first).to_have_attribute('href', re.compile('frame/F2'))


def test_saturation_dot_marks_exposures_with_red_pixels(app_project):
    path, project = applied(app_project)
    capture = project.captures[0].model_copy(deep=True)
    capture.capture_id, capture.exposure_s = 'C4', 40
    project.captures.append(capture)
    project.image_sets[0].capture_ids.append('C4')
    region = project.membrane_regions[0]
    region.band_row_candidates[0].red_pixels_in_crop_by_capture = {'C1': 7}
    next(s for s in project.sources if s.source_asset_id == 'S2').red_overlay_present = True
    project = P.save_project(project, P.locate(path), project.project_revision, 'test_saturation')
    with browser_page(path) as (page, client, url):
        band_stage(page)
        expect(page.locator('.exposure-strip article')).to_have_count(3)
        dot = page.locator('.exposure-strip .sat-dot')
        expect(page.locator('[data-capture="C1"] .sat-dot')).to_have_attribute('title', '飽和画素あり')
        expect(page.locator('[data-capture="C2"] .sat-dot')).to_have_count(1)
        expect(page.locator('[data-capture="C4"] .sat-dot')).to_have_count(0)
        expect(dot).to_have_count(2)




def test_marker_and_overlay_views_map_outlines(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        canvas = page.locator('svg.canvas')
        expect(canvas).to_have_attribute('data-mode', 'lum')
        expect(page.locator('circle[data-handle]')).to_have_count(3)
        page.get_by_role('button', name='マーカー', exact=True).click()
        expect(canvas).to_have_attribute('data-mode', 'marker')
        expect(canvas.locator('image')).to_have_attribute('href', re.compile('frame/F3'))
        expect(page.locator('circle[data-handle]')).to_have_count(0)
        expect(page.locator('polygon.membrane')).to_have_count(2)
        before = api._load(path).model_dump(mode='json')
        page.get_by_role('button', name='Merge', exact=True).click()
        expect(canvas.locator('image')).to_have_count(2)
        expect(page.get_by_role('slider', name='重ね合わせの濃さ')).to_be_visible()
        page.get_by_role('slider', name='重ね合わせの濃さ').fill('0.8')
        expect(canvas.locator('image').nth(1)).to_have_attribute('style', re.compile('opacity: ?0.8'))
        page.get_by_role('button', name='交互表示', exact=True).click()
        expect(canvas.locator('image.flicker')).to_have_count(1)
        assert api._load(path).model_dump(mode='json') == before


def test_export_writes_current_edits_without_review_mode(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        expect(page.get_by_role('group', name='書き出しの種類')).to_have_count(0)
        for kind in ('png', 'pptx'):
            page.get_by_role('button', name=f'{kind.upper()}', exact=True).click()
            pattern = f'{path.name}.png' if kind == 'png' else re.compile(r'_\d{8}-\d{6}-\d{6}[+-]\d{4}\.pptx')
            expect(page.locator('.toast code')).to_contain_text(pattern, timeout=20000)
            saved = Path(page.locator('.toast code').inner_text())
            assert saved.is_file() and saved.parent == P.output_dir(path) / 'exports'
        assert 'data:image' not in page.content()


def test_lane_header_sets_count_and_sample_names(app_project):
    need('set_lanes')
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        expect(page.locator('.lane-name')).to_have_count(0)
        page.get_by_role('button', name='全段のレーンを設定', exact=True).click()
        expect(page.locator('.lane-name')).to_have_count(1)
        page.get_by_role('button', name='全段のレーンを増やす', exact=True).click()
        page.get_by_role('button', name='全段のレーンを増やす', exact=True).click()
        expect(page.locator('.lane-name')).to_have_count(3)
        # The lane count stepper is global: every membrane gets lanes at once.
        assert {r.lane_count for r in api._load(path).membrane_regions} == {3}
        assert page.locator('.lane-name').first.get_attribute('placeholder') == '1'
        # Keep the first save in flight while the next fields are edited.
        page.evaluate("""() => {
          const original = window.fetch;
          window.holdNameSaves = true;
          window.pendingNameSaves = [];
          window.fetch = async (...args) => {
            const response = await original(...args);
            if (window.holdNameSaves && ['set_sample_names', 'set_lanes'].some(op => String(args[0]).endsWith('api/' + op))) {
              await new Promise(resolve => window.pendingNameSaves.push(resolve));
            }
            return response;
          };
        }""")
        names = page.locator('.lane-name')
        names.nth(0).fill('WT')
        names.nth(0).press('Tab')
        page.wait_for_function('window.pendingNameSaves.length === 1')
        names.nth(1).fill('KO')
        names.nth(1).press('Enter')
        page.evaluate('window.pendingNameSaves.shift()()')
        page.wait_for_function('window.pendingNameSaves.length === 1')
        expect(names.nth(1)).to_have_value('KO')
        names.nth(2).fill('KO+')
        names.nth(2).blur()
        page.evaluate("""() => {
          window.holdNameSaves = false;
          window.pendingNameSaves.splice(0).forEach(resolve => resolve());
        }""")
        for _ in range(60):
            groups = api._load(path).sample_groups
            if groups and [s.name for s in groups[0].samples] == ['WT', 'KO', 'KO+']:
                break
            page.wait_for_timeout(50)
        assert [s.name for s in api._load(path).sample_groups[0].samples] == ['WT', 'KO', 'KO+']
        expect(names.nth(1)).to_have_value('KO')
        page.get_by_role('button', name='レーンを減らす', exact=True).click()
        expect(page.locator('.lane-name')).to_have_count(2)
        assert [s.name for s in api._load(path).sample_groups[0].samples] == ['WT', 'KO']




def test_ui_edits_then_exports_without_changing_review_history(app_project):
    need('set_lanes')
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        page.get_by_role('button', name='全段のレーンを設定', exact=True).click()
        page.get_by_role('button', name='全段のレーンを増やす', exact=True).click()
        expect(page.locator('.lane-name')).to_have_count(2)
        for kind in ('png', 'pptx'):
            page.get_by_role('button', name=f'{kind.upper()}', exact=True).click()
            pattern = f'{path.name}.png' if kind == 'png' else re.compile(r'_\d{8}-\d{6}-\d{6}[+-]\d{4}\.pptx')
            expect(page.locator('.toast code')).to_contain_text(pattern, timeout=20000)
            saved = Path(page.locator('.toast code').inner_text())
            assert saved.is_file() and saved.parent == P.output_dir(path) / 'exports'


def test_manual_angle_buttons_and_popover_adjust_each_membrane_independently(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        before = api._load(path)
        a0 = rotation_degrees(before, before.panels[0])
        a1 = rotation_degrees(before, before.panels[1])
        page.get_by_role('button', name='角度を右へ回す', exact=True).click()
        expect(page.get_by_text('編集を保存しました', exact=True)).to_be_visible()
        after = api._load(path)
        assert rotation_degrees(after, after.panels[0]) == pytest.approx(a0 + 0.5, abs=0.05)
        assert rotation_degrees(after, after.panels[1]) == pytest.approx(a1, abs=1e-6)
        # Works in marker mode via the angle popover as well.
        page.get_by_role('button', name='マーカー', exact=True).click()
        page.get_by_role('button', name='角度を調整', exact=True).click()
        slider = page.get_by_role('slider', name='セレクションの角度')
        expect(slider).to_be_visible()
        page.get_by_role('button', name='角度を0度に戻す', exact=True).click()
        expect(page.locator('.pill b')).to_have_text('0°')
        assert rotation_degrees(api._load(path), api._load(path).panels[0]) == pytest.approx(0.0, abs=0.05)


def test_manual_membrane_and_selection_drawing_adds_panels(app_project):
    need('add_manual_membrane', 'add_panel_selection')
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        expect(page.locator('.membrane-item')).to_have_count(2)
        page.get_by_role('button', name='メンブレンを追加', exact=True).click()
        expect(page.locator('svg.canvas')).to_have_attribute('data-fit', 'frame')
        expect(page.locator('svg.canvas')).to_have_attribute('data-draw', 'membrane')
        drag(page, client_point(page, 6, 6), client_point(page, 44, 30), steps=8)
        expect(page.get_by_text('メンブレンを追加しました', exact=True)).to_be_visible()
        expect(page.locator('.membrane-item')).to_have_count(3)
        current = api._load(path)
        assert len(current.membrane_regions) == 3 and len(current.panels) == 2

        band_stage(page)
        page.get_by_role('button', name='セレクションを追加', exact=True).click()
        expect(page.locator('svg.canvas')).to_have_attribute('data-draw', 'selection')
        drag(page, client_point(page, 10, 10), client_point(page, 40, 22), steps=8)
        expect(page.get_by_text('セレクションを追加しました', exact=True)).to_be_visible()
        expect(page.locator('.row')).to_have_count(3)
        updated = api._load(path)
        assert len(updated.panels) == 3
        assert updated.panels[-1].region_id == current.membrane_regions[-1].region_id


def test_proposal_extracts_same_height_colorimetric_marker_without_approval(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        activate_region(page, project.panels[1].region_id)
        marker_img = row(page, 1).locator('[data-testid="marker-preview"] img')
        blot_img = row(page, 1).locator('.blot img')
        expect(marker_img).to_be_visible()
        page.wait_for_function("""() => {
            const r = document.querySelectorAll('.row')[1];
            const b = r?.querySelector('.blot img'), m = r?.querySelector('.marker img');
            return b && m && b.complete && m.complete && b.naturalWidth > 0 && m.naturalWidth > 0 && m.clientHeight > 0;
        }""")
        b_box = blot_img.bounding_box()
        m_box = marker_img.bounding_box()
        assert m_box['x'] >= b_box['x'] + b_box['width'] - 1.5
        assert m_box['height'] == pytest.approx(b_box['height'], abs=1.0)




def test_band_step_switches_membranes_without_leaving_the_stage(app_project):
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        first, second = project.panels
        membrane_tab = page.get_by_role('tab', name='1. メンブレン選択', exact=True)
        band_tab = page.get_by_role('tab', name='2. バンド選択', exact=True)
        expect(membrane_tab).to_have_attribute('aria-selected', 'true')
        expect(band_tab).to_have_attribute('aria-selected', 'false')
        expect(page.locator('.membrane-item')).to_have_count(2)
        expect(page.locator('.membrane-item.active')).to_have_attribute('data-region', first.region_id)
        expect(page.locator('.row')).to_have_count(0)
        expect(page.locator('.exposure-strip')).to_have_count(0)
        expect(page.locator('polygon.candidate')).to_have_count(0)
        expect(page.locator('polygon.chosen')).to_have_count(0)
        expect(page.locator('[data-handle]')).to_have_count(0)
        expect(page.get_by_role('button', name='メンブレンを追加', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='セレクションを追加', exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name='角度を調整', exact=True)).to_have_count(0)
        page.get_by_role('button', name='次へ', exact=True).click()
        expect(band_tab).to_have_attribute('aria-selected', 'true')
        # WBM brand in title and top bar; the project id stays the folder name.
        assert page.title() == 'Western Blot Matome (WBM)'
        expect(page.locator('.brand')).to_have_text('WBM')
        expect(page.locator('.top .id')).to_have_text(project.input_archive.name if project.input_archive else project.project_id)
        expect(page.locator('.row')).to_have_count(2)
        expect(page.locator('.exposure-strip')).to_be_visible()
        expect(page.locator('polygon.chosen')).to_have_count(1)
        expect(page.get_by_role('button', name='メンブレンを追加', exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name='セレクションを追加', exact=True)).to_be_visible()
        # Inactive controls stay guarded, but the image itself selects its membrane.
        expect(row(page, 1).locator('.inline-label')).to_be_disabled()
        row(page, 1).locator('.visual').click()
        expect(page.locator('.row.selected')).to_have_attribute('data-panel', second.panel_id)
        expect(page.locator('.pill')).to_contain_text('M2')
        expect(row(page, 1).locator('.inline-label')).to_be_enabled()
        expect(band_tab).to_have_attribute('aria-selected', 'true')
        expect(page.get_by_role('navigation', name='メンブレンの切り替え')).to_have_count(0)
        row(page, 0).locator('.visual').click()
        expect(page.locator('.row.selected')).to_have_attribute('data-panel', first.panel_id)
        expect(page.locator('.pill')).to_contain_text('M1')
        # Reordering cannot cross into another membrane's row.
        expect(row(page, 0).get_by_role('button', name='下へ', exact=True)).to_be_disabled()
        order = [p.panel_id for p in sorted(api._load(path).panels, key=lambda p: p.display_order)]
        page.locator(f'[data-slot="{order[0]}"] .grip').drag_to(page.locator(f'[data-slot="{order[1]}"] .visual'))
        page.wait_for_timeout(300)
        assert [p.panel_id for p in sorted(api._load(path).panels, key=lambda p: p.display_order)] == order


def test_lane_guides_render_and_drag_saves_per_panel_boundaries(app_project):
    need('set_lanes', 'set_lane_boundaries')
    path, project = applied(app_project)
    with browser_page(path) as (page, client, url):
        band_stage(page)
        expect(page.locator('.lane-edge')).to_have_count(0)
        page.get_by_role('button', name='全段のレーンを設定', exact=True).click()
        page.get_by_role('button', name='全段のレーンを増やす', exact=True).click()
        page.get_by_role('button', name='全段のレーンを増やす', exact=True).click()
        expect(page.locator('.lane-name')).to_have_count(3)
        expect(page.get_by_text('線をドラッグして幅を調整', exact=True)).to_have_count(0)
        expect(row(page, 0).locator('.lane-edge')).to_have_count(4)
        expect(row(page, 0).locator('[data-lane-boundary]')).to_have_count(2)
        # One global count gives every membrane guides; read-only rows show
        # them without drag handles.
        expect(row(page, 1).locator('.lane-edge')).to_have_count(4)
        expect(row(page, 1).locator('[data-lane-boundary]')).to_have_count(0)
        before = api._load(path)
        order = [p.panel_id for p in sorted(before.panels, key=lambda p: p.display_order)]
        equal = [0, 1 / 3, 2 / 3, 1]
        for p in before.panels:
            assert p.display_geometry.lane_layout.boundaries == pytest.approx(equal)
        handle = row(page, 0).locator('[data-lane-boundary="1"]')
        start = centre(handle)
        drag(page, start, [start[0] + 30, start[1]], steps=8)
        expect(page.get_by_text('レーン境界を保存しました', exact=True)).to_be_visible()
        current = api._load(path)
        active = next(p for p in current.panels if p.panel_id == order[0])
        moved = active.display_geometry.lane_layout.boundaries
        assert 1 / 3 < moved[1] < 2 / 3 and moved[0] == 0 and moved[3] == 1
        assert moved[2] == pytest.approx(2 / 3)
        other = next(p for p in current.panels if p.panel_id == order[1])
        assert other.display_geometry.lane_layout.boundaries == pytest.approx(equal)
        # Dragging a lane guide never reorders the rows.
        assert [p.panel_id for p in sorted(current.panels, key=lambda p: p.display_order)] == order
        # Width adjustment stays per membrane: on the other region its own
        # guides are untouched while this membrane keeps its custom widths.
        activate_region(page, project.panels[1].region_id)
        expect(row(page, 1).locator('[data-lane-boundary]')).to_have_count(2)
        expect(row(page, 0).locator('.lane-edge')).to_have_count(4)
        expect(row(page, 0).locator('[data-lane-boundary]')).to_have_count(0)
        current = api._load(path)
        assert current.panels[0].display_geometry.lane_layout.boundaries == pytest.approx(moved)
        assert current.panels[1].display_geometry.lane_layout.boundaries == pytest.approx(equal)


def test_lane_scale_is_fixed_and_individual_count_keeps_other_membrane(app_project):
    path, project = applied(app_project)
    first, second = project.panels
    project = api.set_lanes(path, 9, project.project_revision, sample_names=['S' + str(i) for i in range(9)])
    with browser_page(path) as (page, client, url):
        activate_region(page, second.region_id)
        for count in range(8, 2, -1):
            page.get_by_role('button', name='レーンを減らす', exact=True).click()
            expect(page.locator('.lane-name')).to_have_count(count)
        current = api._load(path)
        a, b = current.panels
        assert a.display_geometry.lane_layout.lane_count == 9
        assert b.display_geometry.lane_layout.lane_count == 3
        assert a.sample_group_id != b.sample_group_id
        assert next(g for g in current.sample_groups if g.sample_group_id == a.sample_group_id).lane_count == 9
        expect(page.get_by_label('図の幅', exact=True)).to_have_count(0)
        assert api._load(path).figure_scale_mode == 'equal_lane_width'
        def widths():
            return [row(page, i).locator('.blot').bounding_box()['width'] for i in range(2)]
        wide, narrow = widths()
        assert narrow / wide == pytest.approx(1 / 3, abs=.005)
        assert api.layout(path)['panel_width_factors'][second.panel_id] == pytest.approx(1 / 3)
        page.reload()
        activate_region(page, second.region_id)
        expect(page.get_by_label('図の幅', exact=True)).to_have_count(0)
        expect(page.locator('.lane-name')).to_have_count(3)
        wide, narrow = widths()
        assert narrow / wide == pytest.approx(1 / 3, abs=.005)


def test_sample_header_follows_selected_membrane_and_hidden_restore_is_region_scoped(app_project):
    path, project = applied(app_project)
    first, second = project.panels
    # Give each membrane its own sample group: 3 lanes for M1, 2 named lanes for M2.
    p = api.set_lanes(path, 3, project.project_revision, panel_id=first.panel_id,
                      sample_names=['A1', 'A2', 'A3'])
    p = api.set_lanes(path, 2, p.project_revision, panel_id=second.panel_id,
                      sample_names=['B1', 'B2'])
    with browser_page(path) as (page, client, url):
        # Selecting M2 in the membrane step shows M2's group header in the band step.
        activate_region(page, second.region_id)
        expect(page.locator('.lane-name')).to_have_count(2)
        expect(page.locator('.lane-editor')).to_have_count(1)
        expect(page.locator('.lane-editor')).to_have_attribute('data-panel', second.panel_id)
        expect(page.locator('.lane-name').nth(0)).to_have_value('B1')
        expect(page.locator('.lane-name').nth(1)).to_have_value('B2')
        # The default count scope is the selected row; M1 keeps its names/count.
        page.get_by_role('button', name='レーンを増やす', exact=True).click()
        expect(page.locator('.lane-name')).to_have_count(3)
        current = api._load(path)
        regions = {r.region_id: r for r in current.membrane_regions}
        assert regions[second.region_id].lane_count == 3
        assert regions[first.region_id].lane_count == 3
        groups = {g.sample_group_id: g for g in current.sample_groups}
        gids = {p.panel_id: p.sample_group_id for p in current.panels}
        assert [s.name for s in groups[gids[first.panel_id]].samples] == ['A1', 'A2', 'A3']
        assert [s.name for s in groups[gids[second.panel_id]].samples] == ['B1', 'B2', None]
        # Editing names on the selected membrane updates only its own group.
        page.locator('.lane-name').nth(2).fill('B3')
        page.locator('.lane-name').nth(2).press('Tab')
        expect(page.locator('.lane-name').nth(2)).to_have_value('B3')
        page.wait_for_timeout(600)  # let the set_sample_names mutation land
        current = api._load(path)
        assert [s.name for s in {g.sample_group_id: g for g in current.sample_groups}[gids[second.panel_id]].samples] == ['B1', 'B2', 'B3']
        assert [s.name for s in {g.sample_group_id: g for g in current.sample_groups}[gids[first.panel_id]].samples] == ['A1', 'A2', 'A3']
        # Header lane cells align with the blot column (74% of the visual row).
        cells_box = page.locator('.lanes .cells').bounding_box()
        blot_box = row(page, 1).locator('.blot').bounding_box()
        assert cells_box['x'] == pytest.approx(blot_box['x'], abs=2)
        assert cells_box['width'] == pytest.approx(blot_box['width'], abs=2)
        assert cells_box['y'] + cells_box['height'] <= blot_box['y']
        first_box = row(page, 0).bounding_box()
        assert cells_box['y'] >= first_box['y'] + first_box['height']


def test_color_and_lum_views_map_mixed_references_without_reordering_membranes(app_project):
    from wb.model import Transform
    path, project = applied(app_project)
    first, second = project.panels
    first.selected_exposure_id = 'C1'
    second_region = next(r for r in project.membrane_regions if r.region_id == second.region_id)
    original_points = copy.deepcopy(second_region.geometry.polygon)
    second_region.reference_frame_id = 'F2'
    second_region.geometry.frame_id = 'F2'
    second_region.geometry.polygon = [[x + 3, y - 2] for x, y in original_points]
    second.display_geometry.reference_frame_id = 'F2'
    second.selected_exposure_id = 'C2'
    rotation = next(t for t in project.transforms if t.transform_id == second.display_geometry.reference_to_rectified_transform_id)
    rotation.from_frame_id = 'F2'
    project.transforms.append(Transform(transform_id='MIXED_MARKER', kind='marker_to_reference',
        from_frame_id='F3', to_frame_id='F2', matrix=[[1,0,3],[0,1,-2],[0,0,1]], status='candidate'))
    next(r for r in project.marker_registrations if r.membrane_region_id == second.region_id).transform_id = 'MIXED_MARKER'
    project.membrane_regions.reverse()
    saved = P.save_project(project, P.locate(path), project.project_revision, 'mixed_reference_fixture')
    before = saved.model_dump(mode='json')
    def points(page, region_id):
        value = page.locator(f'polygon.membrane[data-region="{region_id}"]').get_attribute('points')
        return [list(map(float, point.split(','))) for point in value.split()]
    with browser_page(path) as (page, client, url):
        expect(page.get_by_role('button', name='マーカー', exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('polygon.membrane')).to_have_count(2)
        assert page.locator('.membrane-item').evaluate_all('(items) => items.map(item => item.dataset.region)') == [first.region_id, second.region_id]
        assert points(page, second.region_id) == original_points
        page.get_by_role('button', name='発光', exact=True).click()
        expect(page.locator('polygon.membrane')).to_have_count(2)
        assert points(page, second.region_id) == original_points
        page.get_by_role('button', name='マーカー', exact=True).click()
        page.locator(f'.membrane-item[data-region="{second.region_id}"]').click()
        expect(page.locator('polygon.membrane.selected')).to_have_attribute('data-region', second.region_id)
        expect(page.locator('polygon.membrane')).to_have_count(2)
        assert points(page, second.region_id) == original_points
        assert api._load(path).model_dump(mode='json') == before
