from contextlib import contextmanager
import socket
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright

from wb import api, project as P
from wb.app import create_app
from test_rendering import small_project


@contextmanager
def running_app(project_dir):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(project_dir), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/legacy/"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_legacy_bookmark_opens_current_workbench_without_approval_controls(small_project):
    from playwright.sync_api import expect
    path, _ = small_project
    with running_app(path) as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        expect(page.get_by_role('tab', name='1. メンブレン選択')).to_be_visible()
        expect(page.get_by_role('button', name='PNG', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='確定', exact=True)).to_have_count(0)
        assert not page.url.endswith('/legacy/')
        assert page.request.get(url.replace('/legacy/', '/static/index.html')).status == 404
        browser.close()
