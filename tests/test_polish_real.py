import json
import os
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from test_annotate_ui import running_app
from wb import api

ROOT = Path(__file__).resolve().parents[1]
ZIP = Path(os.environ.get("WB_INPUT_ZIP", ROOT / "private" / "input.zip"))
SHOTS = Path("/tmp/wb-f3ui/shots")


def wait_for_view_image(page):
    page.evaluate("""async () => {
      const image = new Image(); image.src = document.querySelector('svg.canvas image').getAttribute('href');
      await image.decode();
    }""")


PROPOSAL = ROOT / "private" / "real-proposal.json"


@pytest.mark.skipif(not (ZIP.exists() and PROPOSAL.exists()), reason="private real data is unavailable")
def test_real_user_flow(tmp_path, monkeypatch):
    archive = tmp_path / ZIP.name
    archive.symlink_to(ZIP)
    subprocess.run(["uv", "run", "wbm", "init", str(archive)], cwd=ROOT, check=True)
    project = api.P.locate(archive).parent
    proposal = json.loads(PROPOSAL.read_text(encoding="utf-8"))
    monkeypatch.setattr(api, "propose", lambda project_dir: proposal)
    SHOTS.mkdir(parents=True, exist_ok=True)
    with running_app(project) as url, sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
        api.apply_proposal(project, proposal, api._load(project).project_revision)
        page.goto(url)
        page.get_by_role('button', name='次へ', exact=True).click()
        wait_for_view_image(page)
        page.screenshot(path=str(SHOTS / '01-selections.png'))
        before = api._load(project)
        old_panel = before.panels[0]
        page.get_by_role('button', name='Selectionを複製', exact=True).first.click()
        page.get_by_text('Selectionを複製しました', exact=True).wait_for()
        after = api._load(project)
        panel = after.panels[-1]
        assert after.project_revision == before.project_revision + 1
        assert panel.display_geometry.crop_rect == old_panel.display_geometry.crop_rect
        assert panel.selected_exposure_id == old_panel.selected_exposure_id
        page.screenshot(path=str(SHOTS / '04-duplicated.png'))
        page.get_by_role('button', name='Merge', exact=True).click()
        wait_for_view_image(page)
        page.screenshot(path=str(SHOTS / '05-alignment.png'))
        browser.close()
