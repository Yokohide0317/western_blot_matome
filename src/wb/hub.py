"""One localhost server for every opened project.

``/p/<id>/`` is routed to that project's own app (``wb.app.create_app``), so each
project keeps its API unchanged under its prefix. ``/`` lists recent projects.
The recent-projects registry lives in ``project.app_home()``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

from wb import __version__, project as P
from wb.locks import file_lock

HUB_NAME = "wbm-hub"
INDEX_HTML = Path(__file__).parent / "web/hub/index.html"
UPLOAD_LIMIT = 4 * 1024**3
DIALOG_TIMEOUT = 15 * 60
RECENT_LIMIT = 50
_PROJECT_PATH = re.compile(r"^/p/([0-9a-f]{10})(/.*)?$")


def _registry_path() -> Path:
    return P.app_home() / "projects.json"


def _registry_lock():
    return file_lock(P.app_home() / "projects.lock")


def _read() -> dict:
    try:
        data = json.loads(_registry_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("projects"), dict) else {"projects": {}}
    except (OSError, ValueError):
        return {"projects": {}}


def _write(data: dict) -> None:
    path = _registry_path()
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".projects-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def register(project_file) -> dict:
    """Remember an opened project and return its entry (``id``, ``path``, ``name``, ``last_opened``)."""
    location = Path(project_file).resolve()
    entry = {
        "id": P.project_key(location),
        "path": str(location),
        "name": location.parent.name,
        "last_opened": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    }
    with _registry_lock():
        data = _read()
        data["projects"][entry["id"]] = entry
        newest = sorted(data["projects"].values(), key=lambda e: e.get("last_opened", ""), reverse=True)
        data["projects"] = {e["id"]: e for e in newest[:RECENT_LIMIT]}
        _write(data)
    return entry


def _local_time(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return stamp


def recent() -> list[dict]:
    entries = sorted(_read()["projects"].values(), key=lambda e: e.get("last_opened", ""), reverse=True)
    return [{**e, "exists": Path(e["path"]).is_file(), "last_opened_local": _local_time(e.get("last_opened", ""))}
            for e in entries]


def forget(project_id: str) -> bool:
    """Drop a project from the recent list; its files are untouched."""
    with _registry_lock():
        data = _read()
        removed = data["projects"].pop(project_id, None) is not None
        _write(data)
    return removed


def open_path(path) -> dict:
    """Import (if needed) and register a ZIP, image folder or .wblot; return its page."""
    from wb import api

    location, created = api.prepare_project(path, propose_new=True)
    if P.verify_sources(P.load_project(location), location.parent):
        raise ValueError("元画像が変更されたか見つかりません。確認してから開いてください")
    entry = register(location)
    return {"url": f"/p/{entry['id']}/", "id": entry["id"], "path": entry["path"], "created": created}


_DIALOGS = {
    "file": 'POSIX path of (choose file with prompt "ChemiDocのZIP、または .wblot を選んでください")',
    "folder": 'POSIX path of (choose folder with prompt "ChemiDocの画像フォルダを選んでください")',
}


_WINDOWS_DIALOGS = {
    "file": "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            "$d.Filter = 'ChemiDoc (*.zip;*.wblot)|*.zip;*.wblot|All files (*.*)|*.*'; "
            "if ($d.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($d.FileName) }",
    "folder": "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
              "$d.Description = 'ChemiDocの画像フォルダを選んでください'; "
              "if ($d.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }",
}


def _dialog_command(kind: str) -> list[str]:
    if sys.platform == "darwin":
        return ["osascript", "-e", "activate", "-e", _DIALOGS[kind]]
    if os.name == "nt":
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; "
                  "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; " + _WINDOWS_DIALOGS[kind])
        return ["powershell", "-NoProfile", "-STA", "-Command", script]
    raise ValueError("このOSではファイル選択ダイアログを使えません。wbm open <パス> で開いてください")


async def choose_path(kind: str) -> str | None:
    """Show the OS open dialog from the server (macOS or Windows); None when the user cancels."""
    process = await asyncio.create_subprocess_exec(
        *_dialog_command(kind), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(), DIALOG_TIMEOUT)
    if process.returncode:
        if b"-128" in err:  # macOS: user canceled.
            return None
        raise ValueError("ファイル選択ダイアログを開けませんでした: " + err.decode(errors="replace").strip())
    return out.decode("utf-8", errors="replace").strip() or None


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("保存先の名前を決められませんでした")


def lookup(project_id: str) -> Path | None:
    entry = _read()["projects"].get(project_id)
    if entry and Path(entry["path"]).is_file():
        return Path(entry["path"])
    return None


async def _send_text(send, status: int, body: str, content_type="text/plain; charset=utf-8", headers=()):
    data = body.encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(data)).encode()),
                            *headers]})
    await send({"type": "http.response.body", "body": data})


async def _read_body(receive, limit: int) -> bytes:
    chunks, size = [], 0
    while True:
        message = await receive()
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise ValueError("リクエストが大きすぎます")
        chunks.append(chunk)
        if not message.get("more_body"):
            return b"".join(chunks)


class Hub:
    """ASGI app: host/origin checks, the project list at ``/`` and per-project apps under ``/p/<id>/``."""

    def __init__(self):
        self.apps: dict[str, tuple[Path, object]] = {}
        self.last_request = time.monotonic()
        self.on_shutdown = None

    def _project_app(self, project_id: str):
        location = lookup(project_id)
        if location is None:
            return None
        cached = self.apps.get(project_id)
        if cached and cached[0] == location:
            return cached[1]
        from wb.app import create_app

        app = create_app(location)
        self.apps[project_id] = (location, app)
        return app

    @staticmethod
    def _header(scope, name: bytes) -> str:
        return next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == name), "")

    def _allowed(self, scope) -> bool:
        # Only this machine's own pages may drive the server (blocks DNS rebinding and cross-site posts).
        port = scope.get("server", (None, None))[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self._header(scope, b"host") not in hosts:
            return False
        origin = self._header(scope, b"origin")
        unsafe = scope.get("method", "GET") not in ("GET", "HEAD", "OPTIONS")
        return not (unsafe and origin and origin.removeprefix("http://") not in hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            message = await receive()
            while message["type"] != "lifespan.shutdown":
                await send({"type": message["type"] + ".complete"})
                message = await receive()
            await send({"type": "lifespan.shutdown.complete"})
            return
        if scope["type"] != "http":
            return
        self.last_request = time.monotonic()
        if not self._allowed(scope):
            return await _send_text(send, 403, "forbidden")
        path = scope["path"]
        if path == "/api/hub":
            body = json.dumps({"app": HUB_NAME, "pid": os.getpid(), "token": os.environ.get("WBM_HUB_TOKEN"),
                               "version": __version__})
            return await _send_text(send, 200, body, "application/json")
        if path == "/api/hub/update" and scope["method"] == "GET":
            from wb import update

            body = json.dumps(await asyncio.to_thread(update.status), ensure_ascii=False)
            return await _send_text(send, 200, body, "application/json; charset=utf-8")
        if path == "/api/hub/projects":
            return await _send_text(send, 200, json.dumps(recent(), ensure_ascii=False), "application/json; charset=utf-8")
        if path == "/icon.png":
            data = (INDEX_HTML.parent / "icon.png").read_bytes()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"image/png"), (b"content-length", str(len(data)).encode())]})
            return await send({"type": "http.response.body", "body": data})
        if path == "/":
            return await _send_text(send, 200, INDEX_HTML.read_text(encoding="utf-8"), "text/html; charset=utf-8")
        if path.startswith("/api/hub/") and scope["method"] == "POST":
            return await self._action(scope, receive, send, path.removeprefix("/api/hub/"))
        match = _PROJECT_PATH.match(path)
        if not match:
            return await _send_text(send, 404, "not found")
        project_id, rest = match.groups()
        if not rest:
            # Relative asset and API URLs need the trailing slash.
            return await _send_text(send, 307, "", headers=[(b"location", f"/p/{project_id}/".encode())])
        app = self._project_app(project_id)
        if app is None:
            return await _send_text(send, 404, "プロジェクトが見つかりません。wbm open で開き直してください。")
        prefix = f"/p/{project_id}"
        await app({**scope, "root_path": scope.get("root_path", "") + prefix}, receive, send)

    async def _action(self, scope, receive, send, action):
        reply = lambda status, data: _send_text(send, status, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")
        try:
            if action == "upload":
                return await reply(200, await self._upload(scope, receive))
            body = json.loads((await _read_body(receive, 64 * 1024)) or b"{}")
            if action == "open":
                kind = body.get("kind")
                if kind not in _DIALOGS:
                    raise ValueError("kind は file か folder を指定してください")
                chosen = await choose_path(kind)
                if chosen is None:
                    return await reply(200, {"cancelled": True})
                return await reply(200, await asyncio.to_thread(open_path, chosen))
            if action == "shutdown":
                if self.on_shutdown:
                    self.on_shutdown()
                return await reply(200, {"stopping": bool(self.on_shutdown)})
            if action == "update":
                return await self._update(reply)
            if action == "forget":
                return await reply(200, {"removed": forget(str(body.get("id", "")))})
            return await reply(404, {"detail": "操作が見つかりません"})
        except (ValueError, OSError, P.ProjectError) as exc:
            return await reply(422, {"detail": str(exc)})

    async def _update(self, reply):
        """Start the updater (it waits for this process), answer, then stop; the updater restarts the hub."""
        from wb import update

        if not self.on_shutdown:
            raise ValueError("このサーバーは自動で再起動できません。wbm update を使ってください")
        result = await asyncio.to_thread(update.apply, wait_pids=[os.getpid()], restart_hub=True)
        await reply(200, result)
        self.on_shutdown()

    async def _upload(self, scope, receive):
        from urllib.parse import parse_qs

        name = Path(parse_qs(scope.get("query_string", b"").decode()).get("name", [""])[0]).name
        if not name.lower().endswith(".zip") or name.startswith("."):
            raise ValueError("ドロップできるのはChemiDocのZIPだけです")
        destination = _unique(P.library_dir() / name)
        partial = destination.with_name(destination.name + ".part")
        size = 0
        try:
            with partial.open("wb") as out:
                while True:
                    message = await receive()
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > UPLOAD_LIMIT:
                        raise ValueError("ファイルが大きすぎます（4GBまで）")
                    out.write(chunk)
                    if not message.get("more_body"):
                        break
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
        return await asyncio.to_thread(open_path, destination)
