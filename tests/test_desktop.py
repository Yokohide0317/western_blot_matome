"""Desktop lifetime and local app bundle tests; no research images or real state."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

import pytest

from wb import desktop, server, update


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('desktop/server state did not settle')


def test_desktop_adopts_existing_server_and_only_its_session_can_stop_it(monkeypatch):
    monkeypatch.setenv('WBM_IDLE_SECONDS', '.4')
    session = str(uuid.uuid4())
    try:
        initial = server.ensure_server()
        state = server.ensure_server(desktop={'session': session, 'owner_pid': os.getpid()})
        assert state['pid'] == initial['pid']
        assert server.active_desktop_owner()['session'] == session
        time.sleep(1.2)
        assert server._is_ours(state)  # The app owns the lifetime, even without page requests.
        assert not server.stop_server(desktop_session=str(uuid.uuid4()))['stopped']
        assert server._is_ours(state)
        assert server.stop_server(desktop_session=session)['stopped']
        assert server._identity(state['port']) is None
        assert not server._desktop_path().exists()
    finally:
        server.stop_server()


def test_server_stops_if_desktop_process_disappears(monkeypatch):
    monkeypatch.setenv('WBM_IDLE_SECONDS', '60')
    owner = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        state = server.ensure_server(desktop={'session': str(uuid.uuid4()), 'owner_pid': owner.pid})
        owner.terminate()
        owner.wait(timeout=5)
        wait_until(lambda: not server._is_ours(state))
        assert server.active_desktop_owner() is None
    finally:
        if owner.poll() is None:
            owner.terminate()
            owner.wait(timeout=5)
        server.stop_server()


def test_stale_desktop_cannot_restart_after_update():
    script = update.posix_script('/tmp/uv', '/tmp/wbm', '9.0.0', [123], True,
                                 {'session': str(uuid.uuid4()), 'owner_pid': 2147483647})
    assert '--desktop-session' in script and '--owner-pid 2147483647' in script
    with pytest.raises(ValueError, match='終了'):
        server.ensure_server(desktop={'session': str(uuid.uuid4()), 'owner_pid': 2147483647})
    assert not server._state_path().exists()


def test_second_app_session_cannot_take_over_running_owner():
    try:
        first = server.ensure_server(desktop={'session': str(uuid.uuid4()), 'owner_pid': os.getpid()})
        with pytest.raises(ValueError, match='すでに起動'):
            server.ensure_server(desktop={'session': str(uuid.uuid4()), 'owner_pid': os.getpid()})
        assert server._is_ours(first)
    finally:
        server.stop_server()


@pytest.mark.skipif(sys.platform != 'darwin' or bool(os.environ.get('CI')), reason='requires a logged-in macOS desktop')
def test_native_app_stays_open_then_quit_stops_server(tmp_path, monkeypatch):
    monkeypatch.setenv('WBM_IDLE_SECONDS', '.4')
    # Trigger the app's own Quit event, without an external Apple event or
    # requesting macOS Automation permission for the test runner.
    script = desktop.app_script(open_browser=False)
    script = script.replace('        set hubURL to my launchServer()\n', '        set hubURL to my launchServer()\n        delay 4\n        quit\n', 1)
    monkeypatch.setattr(desktop, 'app_script', lambda: script)
    bundle = Path(desktop.install_app(tmp_path / 'WBM.app')['path'])
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    assert info['CFBundleIdentifier'] == desktop.BUNDLE_ID
    assert info['OSAAppletStayOpen'] is True
    assert (bundle / 'Contents/Resources/WBM.icns').is_file()
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True, capture_output=True)
    app = subprocess.Popen([str(bundle / 'Contents/MacOS/applet')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_until(lambda: server._read_state() is not None, timeout=20)
        state = server._read_state()
        wait_until(lambda: server.desktop_owner() is not None)
        assert server.desktop_owner()['owner_pid'] == app.pid
        time.sleep(1.2)
        assert app.poll() is None and server._is_ours(state)
        with urlopen(state['url'], timeout=2) as response:
            assert response.status == 200
        app.wait(timeout=10)
        wait_until(lambda: not server._is_ours(state))
        assert not server._desktop_path().exists()
    finally:
        if app.poll() is None:
            app.terminate()
            app.wait(timeout=5)
        server.stop_server()


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS app bundle')
def test_app_bundle_has_stay_open_flag_and_valid_signature(tmp_path):
    bundle = Path(desktop.install_app(tmp_path / 'WBM.app')['path'])
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    assert info['CFBundleIdentifier'] == desktop.BUNDLE_ID
    assert info['OSAAppletStayOpen']
    assert (bundle / 'Contents/Resources/WBM.icns').is_file()
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True, capture_output=True)


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS app bundle')
def test_installer_refuses_to_overwrite_another_app(tmp_path):
    destination = tmp_path / 'Another.app'
    (destination / 'Contents').mkdir(parents=True)
    with (destination / 'Contents/Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleIdentifier': 'another.application'}, stream)
    before = (destination / 'Contents/Info.plist').read_bytes()
    with pytest.raises(ValueError, match='別のアプリ'):
        desktop.install_app(destination)
    assert (destination / 'Contents/Info.plist').read_bytes() == before


@pytest.mark.skipif(sys.platform != 'darwin' or bool(os.environ.get('CI')), reason='requires a logged-in macOS desktop')
def test_agent_launches_app_and_project_without_a_second_server(tmp_path, monkeypatch):
    import json
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.delenv('WBM_HEADLESS', raising=False)
    script = desktop.app_script(open_browser=False).replace('        set hubURL to my launchServer()\n',
        '        set hubURL to my launchServer()\n        delay 10\n        quit\n', 1)
    monkeypatch.setattr(desktop, 'app_script', lambda: script)
    desktop.install_app(tmp_path / 'WBM.app')
    initial = server.ensure_server()
    command = [sys.executable, '-m', 'wb.cli', 'app', '--no-browser', '--json']
    try:
        def launch(_):
            result = subprocess.run(command, text=True, encoding='utf-8', capture_output=True, timeout=30)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(launch, [1, 2]))
        assert first['pid'] == second['pid'] == initial['pid']
        assert first['app_pid'] == second['app_pid']
        assert server.active_desktop_owner()['owner_pid'] == first['app_pid']
        from test_quantification_api import build_project, synthetic_pixels
        from wb import project as P
        root = tmp_path / 'synthetic-project'
        root.mkdir()
        build_project(root, synthetic_pixels())
        opened = subprocess.run([sys.executable, '-m', 'wb.cli', 'open', str(P.locate(root)), '--no-browser', '--json'],
                                text=True, encoding='utf-8', capture_output=True, timeout=15)
        assert opened.returncode == 0, opened.stderr
        receipt = json.loads(opened.stdout)
        assert receipt['pid'] == initial['pid'] and receipt['app_pid'] == first['app_pid']
        with urlopen(receipt['url'] + 'api/revision', timeout=2) as response:
            assert response.status == 200
        wait_until(lambda: not server._is_ours(initial), timeout=15)
    finally:
        server.stop_server()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows compiler')
def test_windows_app_is_a_native_executable(tmp_path):
    result = desktop.install_app(tmp_path / 'WBM' / 'WBM.exe')
    path = Path(result['path'])
    assert path.read_bytes()[:2] == b'MZ'
    assert (path.parent / 'WBM.ico').is_file()
    assert desktop.installed_app_path() == path


def test_update_keeps_windows_app_as_server_owner():
    session = str(uuid.uuid4())
    script = update.windows_script('uv.exe', 'wbm.exe', '9.0.0', [123], True,
                                   {'session': session, 'owner_pid': 42})
    assert '--desktop-session' in script and session in script and '--owner-pid 42' in script
    assert 'install-skill' not in script


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_app_reuses_live_owner_without_launching_any_process(platform, monkeypatch):
    try:
        state = server.ensure_server(desktop={'session': str(uuid.uuid4()), 'owner_pid': os.getpid()})
        monkeypatch.delenv('WBM_HEADLESS', raising=False)
        monkeypatch.setattr(sys, 'platform', platform)
        monkeypatch.setattr(desktop, 'installed_app_path', lambda: Path('/synthetic/WBM'))
        def unexpected(*args, **kwargs):
            raise AssertionError('a second app/server was launched')
        monkeypatch.setattr(subprocess, 'Popen', unexpected)
        assert desktop.start_app(no_browser=True)['pid'] == state['pid']
    finally:
        server.stop_server()


def test_legacy_server_is_replaced_before_app_claims_it(monkeypatch, capsys):
    initial = server.ensure_server()
    identity = server._identity
    def legacy(port):
        result = identity(port)
        if result and result.get('pid') == initial['pid']:
            result.pop('capabilities', None)
        return result
    monkeypatch.setattr(server, '_identity', legacy)
    try:
        assert desktop.main(['start', '--session', str(uuid.uuid4()), '--owner-pid', str(os.getpid())]) == 0
        assert capsys.readouterr().out.startswith('http://127.0.0.1:')
        current = server._read_state()
        assert current['pid'] != initial['pid'] and server._is_ours(current)
        assert not server._is_ours(initial)
        assert not server.stop_server(expected_token=initial['token'])['stopped']
        assert server._is_ours(current)
    finally:
        server.stop_server()
