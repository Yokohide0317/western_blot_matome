"""Mouse resizing and Excel download through the real UI using synthetic pixels."""
import io
import pytest
from wb import project as P

from openpyxl import load_workbook
from playwright.sync_api import expect

from test_app_ui import browser_page
from test_quantification_api import quant_project, start, configure, compute, LANES, group_of
from test_quantification_placement import select, centers_of


def test_mouse_resize_keeps_centres_and_downloads_excel(quant_project):
    root, client, project, _, _ = quant_project
    project, group = start(client, project)
    moved = [dict(lane) for lane in LANES]
    moved[1].update(x=39, y=28, background_x=38, background_y=9)
    project = configure(client, project, group['group_id'], lanes=moved)
    project = compute(client, project, group['group_id'])
    def centres(g):
        return [(2*l['x']+g['width_px'], 2*l['y']+g['height_px'],
                 2*l['background_x']+g['width_px'], 2*l['background_y']+g['height_px']) for l in g['lanes']]
    before = centres(group_of(project))
    with browser_page(root) as (page, request, url):
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        expect(page.get_by_test_id('quant-excel')).to_be_visible()
        box = page.locator('g.roi.band[data-lane="1"]')
        handle = box.locator('[data-resize="1,1"]')
        expect(handle).to_be_visible()
        rect = box.locator('rect').first.bounding_box()
        grip = handle.bounding_box()
        scale_x, scale_y = rect['width']/10, rect['height']/6
        x, y = grip['x']+grip['width']/2, grip['y']+grip['height']/2
        page.mouse.move(x,y)
        page.mouse.down()
        page.mouse.move(x+2*scale_x,y+scale_y, steps=5)
        page.mouse.up()
        expect(box.locator('rect').first).to_have_attribute('width', '14')
        expect(box.locator('rect').first).to_have_attribute('height', '8')
        expect(page.get_by_test_id('quant-results-empty')).to_be_visible()
        after = request.get(url+'/api/project').json()
        assert centres(group_of(after)) == before
        assert all('width_px' not in l for l in group_of(after)['lanes'])
        page.get_by_role('button', name='計算', exact=True).click()
        expect(page.get_by_test_id('quant-excel')).to_be_visible()
        with page.expect_download() as download:
            page.get_by_test_id('quant-excel').click()
        assert download.value.suggested_filename == 'quantification.xlsx'
        book = load_workbook(io.BytesIO(download.value.path().read_bytes()))
        assert book['定量結果']['C2'].value == 14*8
        assert book['定量結果']['B2'].value == '=1+1'
        assert book['定量結果']['B2'].data_type == 's'
        page.reload()
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        expect(box.locator('rect').first).to_have_attribute('width', '14')
        assert centres(group_of(request.get(url+'/api/project').json())) == before


@pytest.mark.parametrize('missing_kind', ['link', 'display_tiff', 'unrelated_raw'])
def test_no_raw16_explains_why_start_is_disabled(quant_project, missing_kind):
    root, _, _, _, _ = quant_project
    stored = P.load_project(P.locate(root))
    if missing_kind == 'link':
        for capture in stored.captures:
            capture.analysis_source_asset_id = None
    elif missing_kind == 'unrelated_raw':
        # Another capture's raw must not silently replace this panel's image.
        stored.captures[0].analysis_source_asset_id = None
        stored.panels[0].image_set_id = None
        stored.membrane_regions[0].image_set_id = None
    else:
        stored.sources[0].representation_kind = 'presentation_export_candidate'
    P.save_project(stored, P.locate(root), stored.project_revision)
    with browser_page(root) as (page, request, url):
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        expect(page.get_by_test_id('quant-no-raw')).to_contain_text('raw16元画像がないため定量できません')
        expect(page.get_by_role('button', name='定量を開始', exact=True)).to_be_disabled()
        expect(page.get_by_role('combobox', name='定量に使う露光')).to_be_disabled()
        assert request.get(url + '/api/project').json()['quantification_groups'] == []
        # Optional quantification does not disable the figure workflow.
        expect(page.get_by_role('tab', name='2. バンド選択')).to_be_enabled()


def test_missing_raw16_file_disables_saved_group_compute_and_exports(quant_project):
    root, client, project, _, source_path = quant_project
    project, group = start(client, project)
    project = configure(client, project, group['group_id'], lanes=LANES)
    compute(client, project, group['group_id'])
    source_path.unlink()
    with browser_page(root) as (page, _, _):
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        expect(page.locator('.quant [role="alert"]').first).to_contain_text('raw16元画像がないため定量できません')
        expect(page.get_by_role('button', name='計算', exact=True)).to_be_disabled()
        expect(page.get_by_test_id('quant-csv')).to_have_count(0)
        expect(page.get_by_test_id('quant-excel')).to_have_count(0)


def test_invert_changes_display_only_and_preserves_raw_results(quant_project, tmp_path):
    from PIL import Image

    root, client, project, _, source_path = quant_project
    project, group = start(client, project)
    project = configure(client, project, group['group_id'], lanes=LANES)
    compute(client, project, group['group_id'])
    raw_before = source_path.read_bytes()
    with browser_page(root) as (page, request, url):
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        expect(page.get_by_test_id('quant-csv')).to_be_visible()
        before = request.get(url + '/api/project').json()
        csv_before = request.get(url + '/api/quantification_csv/' + group['group_id']).body()
        preview = page.locator('.quant-canvas svg image')
        toggle = page.get_by_role('button', name='白黒反転', exact=True)
        expect(toggle).to_have_attribute('aria-pressed', 'true')
        def background_pixel():
            screenshot = Image.open(io.BytesIO(preview.screenshot())).convert('RGB')
            return screenshot.getpixel((int(screenshot.width * 2 / 96), int(screenshot.height * 50 / 64)))
        inverted = background_pixel()
        assert min(inverted) > 245
        page.screenshot(path=str(tmp_path / 'inverted.png'))
        toggle.click()
        expect(toggle).to_have_attribute('aria-pressed', 'false')
        normal = background_pixel()
        assert max(normal) < 10
        assert inverted == tuple(255 - v for v in normal)
        expect(page.get_by_test_id('quant-csv')).to_be_visible()
        assert request.get(url + '/api/project').json() == before
        assert request.get(url + '/api/quantification_csv/' + group['group_id']).body() == csv_before
        assert source_path.read_bytes() == raw_before
        toggle.focus()
        page.keyboard.press('Space')
        expect(toggle).to_have_attribute('aria-pressed', 'true')
        assert background_pixel() == inverted


def test_simple_ui_computes_without_confirmation_and_keeps_settings_during_roi_save(quant_project):
    root, client, project, _, _ = quant_project
    project, group = start(client, project)
    configure(client, project, group['group_id'], lanes=LANES, source_confirmed=False)
    with browser_page(root) as (page, request, url):
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        pane = page.get_by_test_id('quant-pane')
        expect(pane.get_by_label('枠の幅 (px)')).to_have_count(0)
        expect(pane.get_by_label('枠の高さ (px)')).to_have_count(0)
        expect(pane.locator('input:not([type="checkbox"]):visible')).to_have_count(0)
        expect(pane.get_by_text('確認した', exact=False)).to_have_count(0)
        expect(page.get_by_test_id('quant-readout')).not_to_contain_text('px')
        calculate = page.get_by_role('button', name='計算', exact=True)
        expect(calculate).to_be_enabled()
        calculate.click()
        expect(page.get_by_test_id('quant-excel')).to_be_visible()
        assert group_of(request.get(url + '/api/project').json())['source_confirmed'] is False

        page.get_by_text('計算設定', exact=True).click()
        saturation = page.get_by_label('飽和しきい値（任意）')
        saturation.fill('60000')
        expect(calculate).to_be_disabled()
        box = page.locator('g.roi.band[data-lane="1"]')
        x_before = int(box.get_attribute('data-x'))
        box.focus()
        page.keyboard.press('ArrowRight')
        expect(box).to_have_attribute('data-x', str(x_before + 1))
        page.wait_for_function("async () => (await (await fetch('/api/project')).json()).quantification_groups[0].lanes[0].x === " + str(x_before + 1))
        expect(saturation).to_have_value('60000')
        expect(calculate).to_be_disabled()
        page.get_by_role('button', name='設定を保存', exact=True).click()
        expect(calculate).to_be_enabled()
        calculate.click()
        expect(page.get_by_test_id('quant-excel')).to_be_visible()
        after = group_of(request.get(url + '/api/project').json())
        assert after['saturation_value'] == 60000
        assert after['source_confirmed'] is False

        page.get_by_label('レーン 1 を除外', exact=True).check()
        expect(page.get_by_test_id('quant-results-empty')).to_be_visible()
        expect(calculate).to_be_enabled()
        calculate.click()
        expect(page.get_by_test_id('quant-excel')).to_be_visible()
        excluded = group_of(request.get(url + '/api/project').json())['results']['rows'][0]
        assert excluded['status'] == 'excluded' and excluded['net_signal'] is None


def test_ui_starts_from_selection_and_repositions_saved_group(quant_project, tmp_path):
    import numpy as np
    root, client, _, _, _ = quant_project
    select(root, boundaries=[0, .2, .6, 1])
    with browser_page(root) as (page, request, url):
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        page.get_by_role('button', name='定量を開始', exact=True).click()
        expect(page.get_by_test_id('quant-placement')).to_contain_text('バンド選択から仮配置')
        box = page.locator('g.roi.band[data-lane="1"]')
        expect(box).to_have_attribute('data-y', '25')
        group = group_of(request.get(url + '/api/project').json())
        np.testing.assert_allclose(centers_of(group), [[13.8, 29], [37.2, 29], [68.4, 29]], atol=.5)
        page.get_by_role('button', name='計算', exact=True).click()
        expect(page.get_by_test_id('quant-excel')).to_be_visible()
        select(root, boundaries=[0, .2, .6, 1], crop={'x0': 6, 'y0': 36, 'x1': 84, 'y1': 46})
        page.reload()
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        page.get_by_text('計算設定', exact=True).click()
        page.get_by_role('button', name='バンド選択から枠を再配置', exact=True).click()
        expect(box).to_have_attribute('data-y', '37')
        expect(page.get_by_test_id('quant-results-empty')).to_be_visible()
        assert group_of(request.get(url + '/api/project').json())['group_id'] == group['group_id']
        page.screenshot(path=str(tmp_path / 'auto-placement.png'))
