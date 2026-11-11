"""One detached localhost server (the hub) for every project.

State, lock and log live in ``project.app_home()``. ``open_project`` registers a
project and makes sure the hub runs; the project's page is ``<hub>/p/<id>/``.
The hub stops itself after ``WBM_IDLE_SECONDS`` (default 2 hours) without requests.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import webbrowser

from wb import project as P
from wb.locks import file_lock

WINDOWS = os.name == "nt"

PREFERRED_PORTS = range(8765, 8775)
IDLE_SECONDS = 2 * 60 * 60
_CHILDREN = {}


def _state_path() -> Path:
    return P.app_home() / "server.json"


def _write_state(state, path=None):
    path = _state_path() if path is None else Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".server-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _lock():
    return file_lock(P.app_home() / "server.lock")


def _desktop_path():
    return P.app_home() / 'desktop-session.json'


def desktop_owner():
    try:
        owner = json.loads(_desktop_path().read_text(encoding='utf-8'))
        if not isinstance(owner['owner_pid'], int) or owner['owner_pid'] <= 1 or not isinstance(owner['session'], str):
            return None
        return owner
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _claim_desktop(state, desktop):
    _write_state({**desktop, 'server_token': state['token']}, _desktop_path())


def active_desktop_owner():
    owner, state = desktop_owner(), _read_state()
    if owner and state and owner.get('server_token') == state.get('token') and _alive(owner['owner_pid']) and _is_ours(state):
        return owner
    return None


def _alive(pid: int) -> bool:
    if WINDOWS:
        # os.kill(pid, 0) would terminate the process on Windows.
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_state():
    path = _state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state["pid"], int) or state["pid"] <= 0:
            raise ValueError("invalid pid")
        if not isinstance(state["port"], int) or not 1 <= state["port"] <= 65535:
            raise ValueError("invalid port")
        if not _alive(state["pid"]):
            raise ValueError("not running")
        return state
    except (OSError, ValueError, KeyError, TypeError):
        path.unlink(missing_ok=True)
        return None


def _identity(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/hub", timeout=0.4) as response:
            return json.load(response)
    except (OSError, URLError, ValueError):
        return None


def _is_ours(state):
    """The hub on that port is the one we started: same token and pid."""
    from wb.hub import HUB_NAME

    identity = _identity(state["port"])
    expected = {"app": HUB_NAME, "pid": state["pid"], "token": state.get("token")}
    return isinstance(identity, dict) and all(identity.get(key) == value for key, value in expected.items())


def _listener():
    for port in (*PREFERRED_PORTS, 0):
        listener = socket.socket()
        if not WINDOWS:  # On Windows SO_REUSEADDR would let two servers share a port.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            listener.close()
            continue
        listener.listen(128)
        return listener
    raise ValueError("サーバーのポートを確保できませんでした")


def _spawn(listener, log, env):
    """Start the hub in the background. POSIX hands over the bound socket; Windows passes the port."""
    if WINDOWS:
        port = listener.getsockname()[1]
        listener.close()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen([sys.executable, "-m", "wb.server", "--port", str(port)], env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
    return subprocess.Popen([sys.executable, "-m", "wb.server", "--fd", str(listener.fileno())], env=env,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True, pass_fds=(listener.fileno(),))


def ensure_server(desktop=None):
    """Start the hub unless it already runs; return its state (pid, port, url, started_at)."""
    with _lock():
        if desktop is not None:
            pid, session = desktop.get('owner_pid'), desktop.get('session')
            if not isinstance(pid, int) or pid <= 1 or not _alive(pid) or not isinstance(session, str) or len(session) != 36:
                raise ValueError('WBMアプリが終了しているためサーバーを起動しません')
            previous = desktop_owner()
            if previous and previous['session'] != session and _alive(previous['owner_pid']):
                raise ValueError('WBMアプリはすでに起動しています')
        state = _read_state()
        if state and _is_ours(state):
            if desktop is not None:
                _claim_desktop(state, desktop)
            return state
        _state_path().unlink(missing_ok=True)
        log_path = P.app_home() / "server.log"
        listener = _listener()
        port = listener.getsockname()[1]
        # The pid is read back from the hub: on Windows a venv's python.exe is a
        # launcher whose pid differs from the interpreter actually serving.
        token = secrets.token_hex(8)
        with listener, log_path.open("ab") as log:
            process = _spawn(listener, log, {**os.environ, "WBM_HUB_TOKEN": token})
        state = {
            "pid": None,
            "port": port,
            "url": f"http://127.0.0.1:{port}/",
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "token": token,
        }
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ValueError(f"サーバーを起動できませんでした: {log_path}")
            identity = _identity(port)
            if identity and identity.get("token") == token:
                state["pid"] = identity["pid"]
                _CHILDREN[state["pid"]] = process
                break
            time.sleep(0.05)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise ValueError("サーバーの起動確認が時間切れになりました")
        _write_state(state)
        if desktop is not None:
            _claim_desktop(state, desktop)
        return state


def open_project(project_dir, no_browser=False):
    from wb import hub

    project_file = P.locate(project_dir)
    root = project_file.parent
    if P.verify_sources(P.load_project(project_file), root):
        raise ValueError("元画像が変更されたか見つかりません。確認してから開いてください")
    entry = hub.register(project_file)
    from wb.desktop import start_app
    state = start_app(no_browser=True)
    url = f"{state['url']}p/{entry['id']}/"
    if not no_browser:
        webbrowser.open(url)
    return {**state, "hub_url": state["url"], "url": url, "project_id": entry["id"], "project_path": entry["path"]}


def stop_server(desktop_session=None, expected_token=None):
    """Stop the hub, and with it every open project page."""
    with _lock():
        state = _read_state()
        if expected_token is not None and state and state.get('token') != expected_token:
            return {'stopped': False, 'message': 'サーバーは別の起動に切り替わっています'}
        if desktop_session is not None:
            owner = desktop_owner()
            if not owner or owner['session'] != desktop_session:
                return {'stopped': False, 'message': 'このアプリのサーバーは停止しています'}
            if state and owner.get('server_token') != state.get('token'):
                _desktop_path().unlink(missing_ok=True)
                return {'stopped': False, 'message': 'サーバーは別の起動に切り替わっています'}
        if state and _is_ours(state):
            try:  # Ask politely first; works the same on every OS.
                urlopen(Request(f"http://127.0.0.1:{state['port']}/api/hub/shutdown", data=b"{}", method="POST"),
                        timeout=2).close()
            except (OSError, URLError):
                os.kill(state["pid"], signal.SIGTERM)
            child = _CHILDREN.pop(state["pid"], None)
            if child:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            else:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and _is_ours(state):
                    time.sleep(0.05)
                if _is_ours(state):
                    raise ValueError("サーバーを停止できませんでした")
        _state_path().unlink(missing_ok=True)
        _desktop_path().unlink(missing_ok=True)
    return {"stopped": bool(state), "message": "サーバーを停止しました" if state else "サーバーは停止しています"}


def _serve(fd=None, port=None):
    import uvicorn
    from wb.hub import Hub

    hub = Hub()
    where = {"fd": fd} if fd is not None else {"port": port}
    server = uvicorn.Server(uvicorn.Config(hub, host="127.0.0.1", log_level="warning", **where))
    hub.on_shutdown = lambda: setattr(server, "should_exit", True)
    idle = float(os.environ.get("WBM_IDLE_SECONDS", IDLE_SECONDS))

    def watch():
        while not server.should_exit:
            time.sleep(min(2.0, max(idle / 4, 0.2)))
            owner = desktop_owner()
            if owner and owner.get('server_token') == os.environ.get('WBM_HUB_TOKEN'):
                if not _alive(owner['owner_pid']):
                    server.should_exit = True
                continue
            if time.monotonic() - hub.last_request > idle:
                server.should_exit = True

    threading.Thread(target=watch, daemon=True).start()
    server.run()
    state = _read_state()
    if state and state["pid"] == os.getpid():
        _state_path().unlink(missing_ok=True)


if __name__ == "__main__":
    option, value = sys.argv[1], int(sys.argv[2])
    _serve(**{"fd" if option == "--fd" else "port": value})
