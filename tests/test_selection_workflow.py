import json
import pytest
from playwright.sync_api import expect
from wb import api, project as P
from wb.app import create_app
from fastapi.testclient import TestClient
from test_app_ui import app_project, applied, browser_page, band_stage, row, drag, client_point
from test_rendering import small_project


def test_membrane_flag_is_independent_and_export_uses_both_flags(app_project):
    path, p = applied(app_project)
    first = p.panels[0]
    p = api.set_panel_included(path, first.panel_id, False, p.project_revision)
    p = api.set_membrane_included(path, first.region_id, False, p.project_revision)
    p = api.set_membrane_included(path, first.region_id, True, p.project_revision)
    assert p.membrane_regions[0].included and not p.panels[0].included
    p = api.set_panel_included(path, first.panel_id, True, p.project_revision)
    p = api.set_membrane_included(path, first.region_id, False, p.project_revision)
    assert p.panels[0].included
    assert first.panel_id not in {x.panel_id for x in p.included_panels()}
    assert first.panel_id not in {e.get('panel_id') for page in api.layout(path)['pages'] for e in page['elements']}


def test_legacy_membrane_state_migrates_without_resurrecting_unchecked_selections(app_project):
    path, p = applied(app_project)
    value = p.model_dump()
    region = value['membrane_regions'][0]
    region.pop('included')
    region['suspended_panel_ids'] = [value['panels'][0]['panel_id']]
    value['panels'][0]['included'] = False
    migrated = type(p).model_validate(value)
    assert not migrated.membrane_regions[0].included
    assert migrated.panels[0].included
    assert type(p).model_validate(migrated.model_dump()) == migrated


def test_delete_last_selection_then_manually_add_in_same_membrane(app_project):
    path, p = applied(app_project)
    original = p.panels[0]
    client = TestClient(create_app(path))
    assert client.post('/api/select_band_row', json={}).status_code == 404
    with browser_page(path) as (page, _, url):
        expect(page.locator('.selection-name')).to_have_count(0)
        expect(page.locator('polygon.selection-outline')).to_have_count(0)
        expect(page.get_by_role('checkbox')).to_have_count(0)
        band_stage(page)
        expect(page.locator('polygon.candidate')).to_have_count(0)
        row(page, 0).get_by_role('button', name='Selectionを削除', exact=True).click()
        expect(page.get_by_text('Selectionを削除しました', exact=True)).to_be_visible()
        expect(page.locator('.row')).to_have_count(1)
        current = api._load(path)
        assert original.panel_id not in {x.panel_id for x in current.panels}
        assert current.membrane_regions[0].included
        page.get_by_role('button', name='セレクションを追加', exact=True).click()
        expect(page.locator('svg.canvas')).to_have_attribute('data-draw','selection')
        drag(page, client_point(page, 12, 12), client_point(page, 32, 23), steps=8)
        expect(page.get_by_text('セレクションを追加しました', exact=True)).to_be_visible()
        expect(page.locator('.row')).to_have_count(2)
        created = api._load(path).panels[-1]
        assert created.region_id == original.region_id
        assert created.display_geometry.crop_rect.x0 == pytest.approx(12,abs=.1)
        page.reload()
        expect(page.get_by_role('switch', name='M1 を図に含める')).to_have_attribute('aria-checked','true')


def test_zero_checks_keeps_membrane_on_and_duplicate_is_exact(app_project):
    path, p = applied(app_project)
    with browser_page(path) as (page, _, url):
        band_stage(page)
        check = page.get_by_role('checkbox',name='Selection A を図に含める',exact=True)
        check.uncheck()
        expect(check).not_to_be_checked()
        page.get_by_role('tab',name='1. メンブレン選択').click()
        toggle = page.get_by_role('switch',name='M1 を図に含める',exact=True)
        expect(toggle).to_have_attribute('aria-checked','true')
        toggle.click()
        expect(toggle).to_have_attribute('aria-checked','false')
        toggle.click()
        expect(toggle).to_have_attribute('aria-checked','true')
        band_stage(page)
        expect(check).not_to_be_checked()
        original = api._load(path).panels[0]
        row(page,0).get_by_role('button',name='Selectionを複製',exact=True).click()
        expect(page.get_by_text('Selectionを複製しました',exact=True)).to_be_visible()
        expect(page.locator('.row')).to_have_count(3)
        duplicate = api._load(path).panels[-1]
        assert duplicate.display_geometry.crop_rect == original.display_geometry.crop_rect
        assert duplicate.selected_exposure_id == original.selected_exposure_id
        assert duplicate.included
