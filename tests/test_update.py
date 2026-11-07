"""Update checks and the background updater, without network access or real processes."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from wb import config, hub, update, project as P
from wb.cli import main

REAL_FETCH = update._fetch_latest
WHEEL = "https://github.com/Yokohide0317/western_blot_matome/releases/download/v9.1.0/wbm-9.1.0-py3-none-any.whl"


class Fetch:
    def __init__(self, answer="9.1.0"):
        self.answer, self.calls = answer, 0

    def __call__(self, timeout=5):
        self.calls += 1
        return self.answer


class Spawned:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return object()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No GitHub, no uv: each test says what the network and the installation look like."""
    monkeypatch.setattr(update, "_fetch_latest", Fetch(None))
    monkeypatch.setattr(update, "current", lambda: "0.3.0")
    monkeypatch.setattr(update, "find_uv", lambda: None)

    def no_process(*args, **kwargs):
        raise AssertionError("tests must not start processes")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    monkeypatch.setattr(subprocess, "run", no_process)


@pytest.fixture
def installed(monkeypatch):
    """Pretend this interpreter runs from a uv tool environment."""
    monkeypatch.setattr(update, "find_uv", lambda: "/opt/my tools/uv")
    tool_dir = Path(sys.prefix).parent
    bins = Path("/home/someone/.local/bin")
    monkeypatch.setattr(update, "uv_tool_dir", lambda uv, bin_dir=False: bins if bin_dir else tool_dir)
    spawned = Spawned()
    monkeypatch.setattr(subprocess, "Popen", spawned)
    return spawned


def _config(text):
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("candidate, current, newer", [
    ("0.3.1", "0.3.0", True),
    ("0.10.0", "0.9.9", True),
    ("v1.0", "0.99.99", True),
    ("0.3.0", "0.3.0", False),
    ("0.3", "0.3.0", False),
    ("0.2.9", "0.3.0", False),
    ("nightly", "0.3.0", False),
    ("0.4.0", "0.0.0+local", False),
])
def test_versions_compare_numerically(candidate, current, newer):
    assert update.is_newer(candidate, current) is newer


def test_the_latest_tag_comes_from_the_redirect(monkeypatch):
    from email.message import Message
    from urllib.error import HTTPError

    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append((request.full_url, request.get_method()))
            headers = Message()
            headers["Location"] = "https://github.com/Yokohide0317/western_blot_matome/releases/tag/v9.1.0"
            raise HTTPError(request.full_url, 302, "Found", headers, None)

    monkeypatch.setattr(update, "build_opener", lambda *handlers: Opener())
    assert REAL_FETCH() == "9.1.0"
    assert seen == [(update.LATEST_URL, "HEAD")]

    class Offline:
        def open(self, request, timeout):
            raise OSError("no network")

    monkeypatch.setattr(update, "build_opener", lambda *handlers: Offline())
    assert REAL_FETCH() is None


def test_the_check_is_cached_for_a_day(monkeypatch):
    fetch = Fetch("9.1.0")
    monkeypatch.setattr(update, "_fetch_latest", fetch)
    clock = [1_000_000.0]
    monkeypatch.setattr(update.time, "time", lambda: clock[0])
    assert update.latest() == "9.1.0" and fetch.calls == 1
    clock[0] += 23 * 3600
    fetch.answer = "9.2.0"
    assert update.latest() == "9.1.0" and fetch.calls == 1
    assert json.loads((P.app_home() / "update.json").read_text(encoding="utf-8"))["latest"] == "9.1.0"
    clock[0] += 2 * 3600
    assert update.latest() == "9.2.0" and fetch.calls == 2
    assert update.latest(force=True) == "9.2.0" and fetch.calls == 3


def test_a_failed_check_keeps_the_last_answer_and_retries_within_hours(monkeypatch):
    fetch = Fetch("9.1.0")
    monkeypatch.setattr(update, "_fetch_latest", fetch)
    clock = [1_000_000.0]
    monkeypatch.setattr(update.time, "time", lambda: clock[0])
    update.latest()
    clock[0] += 25 * 3600
    fetch.answer = None
    assert update.latest() == "9.1.0" and fetch.calls == 2
    assert update.latest() == "9.1.0" and fetch.calls == 2  # no retry right away
    clock[0] += 2 * 3600
    fetch.answer = "9.2.0"
    assert update.latest() == "9.2.0" and fetch.calls == 3


def test_check_false_never_touches_the_network(monkeypatch):
    def forbidden(timeout=5):
        raise AssertionError("network used")

    monkeypatch.setattr(update, "_fetch_latest", forbidden)
    _config("[update]\ncheck = false\n")
    info = update.status(force=True)
    assert info["check"] is False and info["latest"] is None and info["available"] is False
    assert "check = false" in info["message"]
    with pytest.raises(ValueError, match="更新はありません"):
        update.apply(wait_pids=[1], restart_hub=False)


@pytest.mark.parametrize("body, message", [
    ("[update]\ncheck = 'yes'\n", "true か false"),
    ("[update]\ncheck = true\nevery = 3\n", "不明な項目"),
])
def test_bad_update_settings(body, message):
    _config(body)
    with pytest.raises(ValueError, match=message):
        config.update_check()
    assert update.enabled() is False  # an unreadable setting stays offline


def test_dev_installs_cannot_update_themselves(monkeypatch):
    monkeypatch.setattr(update, "_fetch_latest", Fetch("9.1.0"))
    info = update.status()
    assert info["available"] and not info["can_apply"] and "uv が見つからない" in info["reason"]
    monkeypatch.setattr(update, "find_uv", lambda: "/opt/uv")
    monkeypatch.setattr(update, "uv_tool_dir", lambda uv, bin_dir=False: Path("/nowhere/uv/tools"))
    info = update.status()
    assert info["can_apply"] is False and "git pull" in info["reason"]
    assert "install.sh" in info["command"] or "install.ps1" in info["command"]
    with pytest.raises(ValueError, match="git pull"):
        update.apply(wait_pids=[1], restart_hub=False)


def test_posix_updater_waits_for_the_processes_then_installs(monkeypatch, installed):
    monkeypatch.setattr(update, "WINDOWS", False)
    monkeypatch.setattr(update, "_fetch_latest", Fetch("9.1.0"))
    assert update.status()["can_apply"] is True
    result = update.apply(wait_pids=[111, 222], restart_hub=True)
    assert result["started"] and result["version"] == "9.1.0"
    (command, options), = installed.calls
    assert command[:2] == ["/bin/sh", "-c"]
    script = command[2]
    assert "for pid in 111 222; do" in script and 'kill -0 "$pid"' in script
    assert script.index("kill -0") < script.index(WHEEL)
    assert f"'/opt/my tools/uv' tool install --force --python 3.12 {WHEEL}" in script
    assert "/home/someone/.local/bin/wbm install-skill" in script
    assert script.rstrip().splitlines()[-2] == "/home/someone/.local/bin/wbm start"
    assert options["start_new_session"] is True and options["stdin"] == subprocess.DEVNULL
    assert "waiting for [111, 222]" in (P.app_home() / "update.log").read_text(encoding="utf-8")


def test_windows_updater_waits_for_the_processes_then_installs(monkeypatch, installed):
    monkeypatch.setattr(update, "WINDOWS", True)
    monkeypatch.setattr(update, "_fetch_latest", Fetch("9.1.0"))
    monkeypatch.setattr(update, "find_uv", lambda: r"C:\Users\O'Brien\.local\bin\uv.exe")
    update.apply(wait_pids=[111, 222], restart_hub=False)
    (command, options), = installed.calls
    assert command[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    script = command[4]
    assert "foreach ($id in @(111, 222)) { Wait-Process -Id $id" in script
    assert script.index("Wait-Process") < script.index(WHEEL)
    assert f"& 'C:\\Users\\O''Brien\\.local\\bin\\uv.exe' tool install --force --python 3.12 '{WHEEL}'" in script
    assert "wbm.exe' install-skill" in script and " start" not in script
    assert options["creationflags"] & update._NO_WINDOW and options["creationflags"] & update._NEW_PROCESS_GROUP
    assert "kill -0" not in script


def test_hub_reports_its_version_and_the_update_status(monkeypatch, installed):
    from wb import __version__

    monkeypatch.setattr(update, "_fetch_latest", Fetch("9.1.0"))
    client = TestClient(hub.Hub(), base_url="http://127.0.0.1:8765")
    assert client.get("/api/hub").json()["version"] == __version__
    info = client.get("/api/hub/update").json()
    assert info["current"] == "0.3.0" and info["latest"] == "9.1.0"
    assert info["available"] is True and info["can_apply"] is True and info["reason"] is None


def test_hub_update_starts_the_updater_then_stops(monkeypatch):
    calls, stopped = [], []
    monkeypatch.setattr(update, "apply", lambda **kwargs: calls.append(kwargs) or {"started": True, "version": "9.1.0"})
    app = hub.Hub()
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    refused = client.post("/api/hub/update", json={})
    assert refused.status_code == 422 and "wbm update" in refused.json()["detail"]
    assert calls == []
    app.on_shutdown = lambda: stopped.append(True)
    response = client.post("/api/hub/update", json={})
    assert response.status_code == 200 and response.json()["version"] == "9.1.0"
    assert calls == [{"wait_pids": [os.getpid()], "restart_hub": True}] and stopped == [True]
    assert client.post("/api/hub/update", json={}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_hub_update_errors_are_shown(monkeypatch):
    def refuse(**kwargs):
        raise ValueError("開発用の導入なので自動更新できません。git pull してください")

    monkeypatch.setattr(update, "apply", refuse)
    app = hub.Hub()
    stopped = []
    app.on_shutdown = lambda: stopped.append(True)
    response = TestClient(app, base_url="http://127.0.0.1:8765").post("/api/hub/update", json={})
    assert response.status_code == 422 and "git pull" in response.json()["detail"] and stopped == []


def test_cli_update_check_json(monkeypatch, capsys):
    fetch = Fetch("9.1.0")
    monkeypatch.setattr(update, "_fetch_latest", fetch)
    assert main(["update", "--check", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["available"] is True and info["latest"] == "9.1.0" and info["can_apply"] is False
    assert main(["update", "--check"]) == 0 and fetch.calls == 2  # --check always asks GitHub
    out = capsys.readouterr().out
    assert "新しいバージョン v9.1.0 があります（現在 v0.3.0）" in out and "install" in out
    fetch.answer = "0.3.0"
    assert main(["update", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["started"] is False


def test_cli_update_without_a_running_hub(monkeypatch, installed, capsys):
    monkeypatch.setattr(update, "WINDOWS", False)
    monkeypatch.setattr(update, "_fetch_latest", Fetch("9.1.0"))
    assert main(["update"]) == 0
    assert "更新を始めました" in capsys.readouterr().out
    (command, _), = installed.calls
    assert f"for pid in {os.getpid()}; do" in command[2] and " start" not in command[2]
