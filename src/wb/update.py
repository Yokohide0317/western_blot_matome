"""Look for a newer GitHub Release and update the installed tool in the background.

The newest tag is read from the redirect of ``/releases/latest`` (no GitHub API,
so no rate limit) and cached in ``project.app_home()/update.json`` for a day.
``[update] check = false`` in config.toml turns every network access off.

Updating replaces the uv tool environment we are running from, so it is done by
a detached process that first waits for the hub and the CLI to exit (on Windows
files in use cannot be replaced), then runs the same install as ``install.sh``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from wb import project as P

WINDOWS = os.name == "nt"
REPO = "Yokohide0317/western_blot_matome"
LATEST_URL = f"https://github.com/{REPO}/releases/latest"
CACHE_SECONDS = 24 * 60 * 60
RETRY_SECONDS = 60 * 60  # after a failed check
WAIT_SECONDS = 120  # how long the updater waits for the old processes to exit
INSTALL_COMMAND = {
    "posix": f"curl -LsSf https://github.com/{REPO}/releases/latest/download/install.sh | sh",
    "nt": "powershell -ExecutionPolicy ByPass -c "
          f"\"irm https://github.com/{REPO}/releases/latest/download/install.ps1 | iex\"",
}
# Spelled out so tests can build the Windows command on any OS.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_TAG = re.compile(r"/releases/tag/v?(\d+(?:\.\d+)*)/?$")


def current() -> str:
    from wb import __version__

    return __version__


def parse_version(text) -> tuple[int, ...] | None:
    """``"v0.10.2"`` → ``(0, 10, 2)``; None for anything that is not plain dotted numbers."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate, than) -> bool:
    new, old = parse_version(candidate), parse_version(than)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def wheel_url(version: str) -> str:
    return f"https://github.com/{REPO}/releases/download/v{version}/wbm-{version}-py3-none-any.whl"


def install_command() -> str:
    return INSTALL_COMMAND["nt" if WINDOWS else "posix"]


def log_path() -> Path:
    return P.app_home() / "update.log"


def _cache_path() -> Path:
    return P.app_home() / "update.json"


def _read_cache() -> dict | None:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("checked_at"), (int, float)):
            return data
    except (OSError, ValueError):
        pass
    return None


def _write_cache(data: dict) -> None:
    path = _cache_path()
    try:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".update-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except OSError:
        pass  # A missing cache only means checking again.


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Keep the 302 so its Location header can be read.


def _fetch_latest(timeout: float = 5) -> str | None:
    """Newest release version from the ``/releases/latest`` redirect, or None when unknown."""
    request = Request(LATEST_URL, method="HEAD", headers={"User-Agent": f"wbm/{current()}"})
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            location = response.geturl()
    except HTTPError as exc:
        location = exc.headers.get("Location", "") if exc.headers else ""
    except (OSError, ValueError):
        return None
    match = _TAG.search(location or "")
    return match.group(1) if match else None


def enabled() -> bool:
    from wb import config

    try:
        return config.update_check()
    except ValueError:
        return False  # An unreadable config file: stay offline rather than guess.


def latest(force: bool = False) -> str | None:
    """Newest released version (``"0.3.1"``), checked at most once a day; None when unknown or turned off."""
    if not enabled():
        return None
    cache = _read_cache()
    now = time.time()
    if cache and not force:
        age = now - cache["checked_at"]
        if 0 <= age < (RETRY_SECONDS if cache.get("failed") else CACHE_SECONDS):
            return cache.get("latest")
    found = _fetch_latest()
    if found is None:
        known = cache.get("latest") if cache else None
        _write_cache({"latest": known, "checked_at": now, "failed": True})
        return known
    _write_cache({"latest": found, "checked_at": now})
    return found


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / ("uv.exe" if WINDOWS else "uv")
    return str(candidate) if candidate.is_file() else None


def _hidden() -> dict:
    return {"creationflags": _NO_WINDOW} if WINDOWS else {}


def uv_tool_dir(uv: str, bin_dir: bool = False) -> Path | None:
    command = [uv, "tool", "dir", *(["--bin"] if bin_dir else [])]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20,
                                stdin=subprocess.DEVNULL, **_hidden())
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    return Path(text) if result.returncode == 0 and text else None


def _installer() -> tuple[str | None, str | None]:
    """``(uv, reason)``: the uv that installed this copy, or why it cannot update itself."""
    uv = find_uv()
    if uv is None:
        return None, "uv が見つからないので自動更新できません。インストールの1行をもう一度実行してください"
    tools = uv_tool_dir(uv)
    try:
        installed = tools is not None and Path(sys.prefix).resolve().is_relative_to(tools.resolve())
    except OSError:
        installed = False
    if not installed:
        return None, "開発用の導入なので自動更新できません。git pull してください"
    return uv, None


def status(force: bool = False) -> dict:
    """``{current, latest, available, can_apply, reason, ...}``; never raises for network problems."""
    now, on = current(), enabled()
    found = latest(force) if on else None
    available = bool(found) and is_newer(found, now)
    uv, reason = _installer()
    if not on:
        message = f"更新の確認は止めています（config.toml の [update] check = false）。現在 v{now}"
    elif found is None:
        message = f"最新版を確認できませんでした（現在 v{now}）"
    elif available:
        how = "wbm update で更新できます" if uv else reason
        message = f"新しいバージョン v{found} があります（現在 v{now}）。{how}"
    else:
        message = f"最新版です（v{now}）"
    return {
        "current": now,
        "latest": found,
        "available": available,
        "can_apply": uv is not None,
        "reason": reason,
        "check": on,
        "command": install_command(),
        "log": str(log_path()),
        "message": message,
    }


def posix_script(uv: str, wbm: str, version: str, wait_pids, restart_hub: bool, desktop=None) -> str:
    q = shlex.quote
    steps = [
        f"for pid in {' '.join(str(int(p)) for p in wait_pids)}; do",
        "  n=0",
        f"  while kill -0 \"$pid\" 2>/dev/null && [ \"$n\" -lt {WAIT_SECONDS * 2} ]; do sleep 0.5; n=$((n+1)); done",
        "done",
        "sleep 1",
        f"echo \"[$(date)] installing wbm {version}\"",
        f"{q(uv)} tool install --force --python 3.12 {q(wheel_url(version))} || exit 1",
    ]
    if restart_hub:
        options = f" --desktop-session {q(desktop['session'])} --owner-pid {int(desktop['owner_pid'])}" if desktop else ''
        steps.append(f"{q(wbm)} start{options}")
    steps.append("echo \"[$(date)] done\"")
    return "\n".join(steps)


def windows_script(uv: str, wbm: str, version: str, wait_pids, restart_hub: bool, desktop=None) -> str:
    q = lambda text: "'" + str(text).replace("'", "''") + "'"
    ids = ", ".join(str(int(p)) for p in wait_pids)
    steps = [
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        f"foreach ($id in @({ids})) {{ Wait-Process -Id $id -Timeout {WAIT_SECONDS} -ErrorAction SilentlyContinue }}",
        "Start-Sleep -Seconds 2",  # uv's launcher processes exit just after the interpreter.
        f"Write-Output \"[$(Get-Date)] installing wbm {version}\"",
        f"& {q(uv)} tool install --force --python 3.12 {q(wheel_url(version))}",
        "if ($LASTEXITCODE) { exit $LASTEXITCODE }",
    ]
    if restart_hub:
        options = f" --desktop-session {q(desktop['session'])} --owner-pid {int(desktop['owner_pid'])}" if desktop else ''
        steps.append(f"& {q(wbm)} start{options}")
    steps.append("Write-Output \"[$(Get-Date)] done\"")
    return "; ".join(steps)


def apply(wait_pids, restart_hub: bool, version: str | None = None, desktop=None) -> dict:
    """Start the detached updater and return at once; it installs ``version`` (default: the latest)."""
    now = current()
    version = version or latest()
    if not version or not is_newer(version, now):
        raise ValueError(f"更新はありません（現在 v{now}）")
    uv, reason = _installer()
    if uv is None:
        raise ValueError(reason)
    bin_dir = uv_tool_dir(uv, bin_dir=True)
    if bin_dir is None:
        raise ValueError("uv の実行ファイルの場所を確認できませんでした")
    wbm = str(bin_dir / ("wbm.exe" if WINDOWS else "wbm"))
    pids = [int(p) for p in wait_pids if p]
    log = log_path()
    with log.open("a", encoding="utf-8") as out:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out.write(f"\n[{stamp}] update {now} -> {version}, waiting for {pids}\n")
    # Run from the home folder: a working directory inside the tool environment would be in use.
    from wb import server
    desktop = desktop or server.active_desktop_owner()
    with log.open("ab") as out:
        if WINDOWS:
            # Not DETACHED_PROCESS: uv and wbm would then each open a visible console window.
            # A hidden console of its own (CREATE_NO_WINDOW) outlives the hub just the same.
            flags = _NEW_PROCESS_GROUP | _NO_WINDOW
            command = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                       windows_script(uv, wbm, version, pids, restart_hub, desktop)]
            subprocess.Popen(command, cwd=Path.home(), stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                             creationflags=flags)
        else:
            command = ["/bin/sh", "-c", posix_script(uv, wbm, version, pids, restart_hub, desktop)]
            subprocess.Popen(command, cwd=Path.home(), stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                             start_new_session=True)
    return {"started": True, "current": now, "version": version, "log": str(log),
            "message": "更新を始めました。1分ほどで完了します"}


def update_now() -> dict:
    """``wbm update``: stop the hub, then update in the background (the hub restarts if it was running)."""
    from wb import server

    info = status(force=True)
    if not info["available"]:
        return {**info, "started": False}
    if not info["can_apply"]:
        raise ValueError(f"{info['reason']}（新しいバージョン v{info['latest']}）")
    state = server._read_state()
    desktop = server.active_desktop_owner()
    hub_pid = state["pid"] if state and server._is_ours(state) else None
    if hub_pid:
        server.stop_server()
    options = {'desktop': desktop} if desktop else {}
    result = apply(wait_pids=[hub_pid, os.getpid()], restart_hub=bool(hub_pid), version=info["latest"], **options)
    if hub_pid:
        result["message"] += "。サーバーは更新後に起動し直します（開いていた画面は再読み込みしてください）"
    return result
