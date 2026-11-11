"""One-download, multi-sheet quantification exports from synthetic raw pixels."""
import io
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from wb import api, project as P
from wb.cli import main
from test_quantification_api import quant_project, post, start, configure, compute, LANES, group_of


def multi_group_project(client, project):
    ids = []
    for polarity in ['high', 'low']:
        project, group = start(client, project)
        ids.append(group['group_id'])
        project = configure(client, project, group['group_id'], lanes=LANES, signal_polarity=polarity)
        project = compute(client, project, group['group_id'])
    project = post(client, 'duplicate_panel', project, panel_id='PANEL_K')
    new_panel = project['panels'][-1]['panel_id']
    project = post(client, 'start_quantification', project, panel_id=new_panel, capture_id='C2')
    gid = project['quantification_groups'][-1]['group_id']
    ids.append(gid)
    project = configure(client, project, gid, lanes=LANES)
    project = compute(client, project, gid)
    project = post(client, 'reorder_panels', project, panel_ids=[new_panel, 'PANEL_K'])
    return project, ids


def test_all_groups_in_separate_tabs_with_exact_values_and_conditions(quant_project):
    _, client, project, _, source = quant_project
    project, ids = multi_group_project(client, project)
    before = source.read_bytes()
    response = client.get('/api/quantification_excel')
    assert response.status_code == 200, response.text
    assert 'spreadsheetml.sheet' in response.headers['content-type']
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ['A (2s)', 'TargetA (1s)', 'TargetA (1s) (2)', '解析条件']
    assert book.active.title == book.sheetnames[0]
    for sheet, gid in zip(book.worksheets[:-1], [ids[2], ids[0], ids[1]]):
        rows = group_of(project, gid)['results']['rows']
        assert sheet['B2'].value == '=1+1' and sheet['B2'].data_type == 's'
        assert sheet['F2'].value == rows[0]['net_signal']
        assert sheet['F3'].value == rows[1]['net_signal']
        assert sheet['I2'].value == LANES[0]['x']
        assert sheet.freeze_panes == 'A2'
    conditions = {}
    for name, key, value in book['解析条件'].iter_rows(min_row=2, values_only=True):
        conditions.setdefault(name, {})[key] = value
    for name, gid in zip(book.sheetnames[:-1], [ids[2], ids[0], ids[1]]):
        assert conditions[name]['group_id'] == gid
        assert conditions[name]['source_sha256'] == project['sources'][0]['sha256']
        assert conditions[name]['fingerprint'] == group_of(project, gid)['results']['fingerprint']
    assert source.read_bytes() == before
    assert client.get('/api/project').json() == project  # Downloads do not edit the project.


def test_sheet_names_are_valid_unique_and_bounded(quant_project):
    _, client, project, _, _ = quant_project
    project, _ = multi_group_project(client, project)
    for panel in project['panels']:
        project = post(client, 'rename_panel', project, panel_id=panel['panel_id'], label="'Target/[]:*?" + '長い名前'*12)
    response = client.get('/api/quantification_excel')
    assert response.status_code == 200, response.text
    names = load_workbook(io.BytesIO(response.content)).sheetnames
    assert len(names) == len({name.casefold() for name in names}) == 4
    assert all(0 < len(name) <= 31 for name in names)
    assert all(not set('[]:*?/\\') & set(name) for name in names)
    assert all(not name.startswith("'") and not name.endswith("'") for name in names)


@pytest.mark.parametrize('unready', ['uncalculated', 'stale', 'missing_raw'])
def test_batch_never_silently_omits_or_exports_unready_groups(quant_project, unready):
    root, client, project, _, source = quant_project
    project, ids = multi_group_project(client, project)
    if unready == 'uncalculated':
        project, _ = start(client, project)
    elif unready == 'stale':
        stored = api._load(root)
        stored.quantification_groups[1].width_px += 2
        P.save_project(stored, P.locate(root), stored.project_revision)
    else:
        source.unlink()
    response = client.get('/api/quantification_excel')
    assert response.status_code == 422
    assert '定量を出力できません' in response.json()['detail']
    if unready == 'missing_raw':
        assert 'raw16' in response.json()['detail']


def test_batch_rejects_empty_project(quant_project):
    _, client, _, _, _ = quant_project
    assert client.get('/api/quantification_excel').status_code == 422


def test_cli_all_exports_one_workbook(quant_project, capsys):
    root, client, project, _, _ = quant_project
    multi_group_project(client, project)
    args = ['--project', str(root), '--json']
    assert main(['quantify', 'export', '--all', *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['group_count'] == 3
    book = load_workbook(result['path'])
    assert len(book.sheetnames) == 4
    assert Path(result['path']).parent == root / 'wblot_output' / 'exports'
    assert main(['quantify', 'export', '--all', '--format', 'csv', *args]) == 1
    assert 'Excel' in json.loads(capsys.readouterr().out)['error']
