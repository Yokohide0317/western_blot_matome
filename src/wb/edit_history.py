"""Local edit snapshots, referenced by the atomically saved project.

Snapshots are written before their references are committed. A failed project
save therefore cannot consume an undo/redo step. Research data stays local.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from wb.model import EditHistory, Project

LIMIT = 50


def directory(path):
    from wb.project import app_home
    key = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()
    return app_home() / 'edit-history' / key


def content(project):
    return project.model_dump(mode='json', exclude={'history', 'project_revision', 'edit_history'})


def snapshot(path, project):
    data = json.dumps(content(project), ensure_ascii=False, sort_keys=True).encode('utf-8')
    key = hashlib.sha256(data).hexdigest()
    folder = directory(path)
    folder.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=folder, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, folder / (key + '.json'))
    finally:
        Path(temp).unlink(missing_ok=True)
    return key


def restore(path, current, direction):
    history = current.edit_history.model_copy(deep=True)
    stack = getattr(history, direction)
    label = '元に戻す' if direction == 'undo' else 'やり直す'
    if not stack:
        raise ValueError(f'{label}操作がありません')
    try:
        data = (directory(path) / (stack[-1] + '.json')).read_bytes()
        if hashlib.sha256(data).hexdigest() != stack[-1]:
            raise ValueError('履歴ファイルが変更されています')
        saved = Project.model_validate(json.loads(data))
        if saved.project_id != current.project_id:
            raise ValueError('別のプロジェクトの履歴です')
    except (OSError, ValueError) as exc:
        raise ValueError(f'履歴を読み込めないため、{label}ことができません') from exc
    stack.pop()
    other = getattr(history, 'redo' if direction == 'undo' else 'undo')
    other.append(snapshot(path, current))
    del other[:-LIMIT]
    saved.edit_history = history
    saved.history = current.history.copy()
    return saved


def record(path, before, after, operation):
    if before is None or operation in {'refresh_project', 'import_project'}:
        after.edit_history = EditHistory()
    elif content(before) == content(after):
        after.edit_history = before.edit_history.model_copy(deep=True)
    else:
        after.edit_history = EditHistory(undo=[*before.edit_history.undo, snapshot(path, before)][-LIMIT:])


def status(path, project):
    folder = directory(path)
    return {'project_revision': project.project_revision, **{
        'can_' + direction: bool(stack and (folder / (stack[-1] + '.json')).is_file())
        for direction in ('undo', 'redo') for stack in [getattr(project.edit_history, direction)]}}


def prune(path, project):
    keep = set(project.edit_history.undo + project.edit_history.redo)
    for file in directory(path).glob('*.json'):
        if file.stem not in keep:
            try:
                file.unlink()
            except OSError:
                pass  # Cleanup must not make a successfully saved edit look failed.
