"""Undo/redo persistence, atomic failures and stale writers with synthetic data."""
from concurrent.futures import ThreadPoolExecutor
import os

import pytest
from fastapi.testclient import TestClient

from wb import api, edit_history as H, project as P
from wb.app import create_app
from test_quantification_api import quant_project, start, configure, compute, LANES, group_of, post


@pytest.fixture
def clean_history(quant_project):
    root, client, _, pixels, source = quant_project
    current = api._load(root)
    current = P.save_project(current, P.locate(root), current.project_revision, 'import_project')
    return root, client, current.model_dump(mode='json'), pixels, source


def saved_content(data):
    return {k: v for k, v in data.items() if k not in {'project_revision', 'history', 'edit_history'}}


def test_undo_redo_persist_restore_deleted_group_and_keep_raw_bytes(clean_history):
    root, client, initial, _, source = clean_history
    before_raw = source.read_bytes()
    assert client.get('/api/edit_history').json() == dict(project_revision=initial['project_revision'], can_undo=False, can_redo=False)
    project, group = start(client, initial)
    project = configure(client, project, group['group_id'], lanes=LANES)
    project = compute(client, project, group['group_id'])
    calculated = project
    project = post(client, 'delete_panel', project, panel_id='PANEL_K')
    assert project['quantification_groups'] == []
    project = post(client, 'undo', project)
    assert saved_content(project) == saved_content(calculated)
    assert project['history'][-1]['operation'] == 'undo'
    assert project['project_revision'] == calculated['project_revision'] + 2
    assert client.get('/api/quantification_info/' + group['group_id']).json()['results_current']
    # A fresh app instance sees the same local redo stack.
    client = TestClient(create_app(root))
    assert client.get('/api/edit_history').json()['can_redo']
    redone = post(client, 'redo', project)
    assert redone['panels'] == [] and redone['quantification_groups'] == []
    assert len(redone['history']) == len(project['history']) + 1
    assert source.read_bytes() == before_raw


def test_multiple_steps_branch_and_noop_preserves_redo(clean_history):
    _, client, initial, _, _ = clean_history
    a = post(client, 'rename_panel', initial, panel_id='PANEL_K', label='First')
    b = post(client, 'rename_panel', a, panel_id='PANEL_K', label='Second')
    undo = post(client, 'undo', b)
    assert saved_content(undo) == saved_content(a)
    undo = post(client, 'undo', undo)
    assert saved_content(undo) == saved_content(initial)
    status = client.get('/api/edit_history').json()
    assert not status['can_undo'] and status['can_redo']
    noop = post(client, 'rename_panel', undo, panel_id='PANEL_K', label=initial['panels'][0]['label'])
    assert client.get('/api/edit_history').json()['can_redo']
    redone = post(client, 'redo', noop)
    assert saved_content(redone) == saved_content(a)
    branch = post(client, 'rename_panel', redone, panel_id='PANEL_K', label='Branch')
    assert not client.get('/api/edit_history').json()['can_redo']
    bad = client.post('/api/redo', json=dict(expected_revision=branch['project_revision']))
    assert bad.status_code == 422
    assert client.get('/api/project').json() == branch


@pytest.mark.parametrize('direction', ['undo', 'redo'])
def test_failed_project_replace_does_not_consume_step(clean_history, monkeypatch, direction):
    root, client, initial, _, _ = clean_history
    a = post(client, 'rename_panel', initial, panel_id='PANEL_K', label='First')
    if direction == 'redo':
        a = post(client, 'undo', a)
    path = P.locate(root)
    before = path.read_bytes()
    real_replace = os.replace
    def fail_project(src, dst):
        if dst == path:
            raise OSError('synthetic write failure')
        return real_replace(src, dst)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', fail_project)
        with pytest.raises(OSError):
            getattr(api, direction)(root, a['project_revision'])
    assert path.read_bytes() == before
    assert post(client, direction, a)['project_revision'] == a['project_revision'] + 1


def test_stale_and_concurrent_writers_cannot_overwrite_each_other(clean_history):
    root, client, initial, _, _ = clean_history
    revision = initial['project_revision']
    def rename(label):
        try:
            return api.rename_panel(root, 'PANEL_K', label, revision)
        except P.RevisionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(rename, ['First', 'Second']))
    assert sum(p is not None for p in saved) == 1
    before = client.get('/api/project').json()
    bad = client.post('/api/undo', json=dict(expected_revision=revision))
    assert bad.status_code == 409
    assert client.get('/api/project').json() == before
    undone = post(client, 'undo', before)
    assert saved_content(undone) == saved_content(initial)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_unavailable_snapshot_keeps_project_intact(clean_history, damage):
    root, client, initial, _, _ = clean_history
    a = post(client, 'rename_panel', initial, panel_id='PANEL_K', label='First')
    file = H.directory(P.locate(root)) / (a['edit_history']['undo'][-1] + '.json')
    if damage == 'missing':
        file.unlink()
        assert not client.get('/api/edit_history').json()['can_undo']
    else:
        file.write_text('{}', encoding='utf-8')
    bad = client.post('/api/undo', json=dict(expected_revision=a['project_revision']))
    assert bad.status_code == 422
    assert client.get('/api/project').json() == a


def test_history_limit_and_cleanup_and_no_image_folder_sidecars(clean_history):
    root, client, project, _, _ = clean_history
    files = set(root.iterdir())
    for i in range(H.LIMIT + 3):
        project = post(client, 'rename_panel', project, panel_id='PANEL_K', label=f'Label {i}')
    assert len(project['edit_history']['undo']) == H.LIMIT
    assert len(list(H.directory(P.locate(root)).glob('*.json'))) == H.LIMIT
    for _ in range(H.LIMIT):
        project = post(client, 'undo', project)
    assert not client.get('/api/edit_history').json()['can_undo']
    assert len(project['edit_history']['redo']) == H.LIMIT
    assert len(list(H.directory(P.locate(root)).glob('*.json'))) == H.LIMIT
    assert set(root.iterdir()) == files
