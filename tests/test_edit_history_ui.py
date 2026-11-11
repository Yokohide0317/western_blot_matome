"""Saved edits undo/redo through the real UI, without intercepting text undo."""
from playwright.sync_api import expect

from wb import project as P
from test_app_ui import app_project, applied, browser_page
from test_rendering import small_project
from test_quantification_api import quant_project, start, configure, LANES, group_of


def test_header_history_buttons_reload_branch_and_shortcuts(app_project):
    root, project = applied(app_project)
    P.save_project(project, P.locate(root), project.project_revision, 'import_project')
    original = project.panels[0].label
    with browser_page(root) as (page, request, url):
        undo = page.get_by_role('button', name='元に戻す', exact=True)
        redo = page.get_by_role('button', name='やり直す', exact=True)
        expect(undo).to_be_disabled()
        expect(redo).to_be_disabled()
        page.get_by_role('tab', name='2. バンド選択').click()
        label = page.locator('.inline-label').first
        label.fill('First')
        label.press('Enter')
        expect(undo).to_be_enabled()
        undo.click()
        expect(label).to_have_value(original)
        expect(undo).to_be_disabled()
        expect(redo).to_be_enabled()
        page.reload()
        page.get_by_role('tab', name='2. バンド選択').click()
        expect(redo).to_be_enabled()
        redo.click()
        expect(label).to_have_value('First')
        expect(redo).to_be_disabled()
        page.locator('body').click(position={'x': 1, 'y': 1})
        page.keyboard.press('Control+z')
        expect(label).to_have_value(original)
        expect(redo).to_be_enabled()
        page.keyboard.press('Control+Shift+z')
        expect(label).to_have_value('First')
        expect(redo).to_be_disabled()
        # A text field's native undo must not consume project history.
        before = request.get(url + '/api/project').json()['project_revision']
        label.focus()
        page.keyboard.press('Control+z')
        assert request.get(url + '/api/project').json()['project_revision'] == before
        label.press('Escape')
        page.locator('body').click(position={'x': 1, 'y': 1})
        undo.click()
        expect(label).to_have_value(original)
        label.fill('Branch')
        label.press('Enter')
        expect(undo).to_be_enabled()
        expect(redo).to_be_disabled()


def test_quantification_drag_is_one_undo_step_and_redo_restores_it(quant_project):
    root, client, project, _, source = quant_project
    project, group = start(client, project)
    configure(client, project, group['group_id'], lanes=LANES)
    initial = P.load_project(P.locate(root))
    P.save_project(initial, P.locate(root), initial.project_revision, 'import_project')
    raw_before = source.read_bytes()
    with browser_page(root) as (page, request, url):
        page.get_by_role('tab', name='3. 定量(Optional)').click()
        undo = page.get_by_role('button', name='元に戻す', exact=True)
        redo = page.get_by_role('button', name='やり直す', exact=True)
        expect(undo).to_be_disabled()
        roi = page.locator('g.roi.band[data-lane="1"]')
        rect = roi.locator('rect').first.bounding_box()
        x, y = rect['x'] + rect['width']/2, rect['y'] + rect['height']/2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + rect['width']/2, y, steps=5)
        expect(undo).to_be_disabled()
        page.mouse.up()
        expect(undo).to_be_enabled()
        moved = group_of(request.get(url + '/api/project').json())
        assert moved['lanes'][0]['x'] == LANES[0]['x'] + 5
        undo.click()
        expect(roi).to_have_attribute('data-x', str(LANES[0]['x']))
        expect(undo).to_be_disabled()
        expect(redo).to_be_enabled()
        redo.click()
        expect(roi).to_have_attribute('data-x', str(moved['lanes'][0]['x']))
        expect(redo).to_be_disabled()
        assert source.read_bytes() == raw_before
