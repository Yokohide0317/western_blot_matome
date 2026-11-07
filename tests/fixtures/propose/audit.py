"""Reproduce a proposal audit on an already prepared project and save its overlays.

Usage: uv run python tests/fixtures/propose/audit.py /tmp/wb-f3/d1 --prefix d1
"""
import argparse
import importlib.util
import json
from pathlib import Path
import time

from wb.project import load_project
from wb.propose import propose_layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--prefix', required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('propose_tests', Path(__file__).parents[2] / 'test_propose.py')
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    project = load_project(args.project / 'project.json')
    start = time.perf_counter()
    proposal = propose_layout(project, args.project)
    runtime = time.perf_counter() - start
    output = Path('/tmp/wb-f3')
    output.mkdir(exist_ok=True)
    (output / f'{args.prefix}-v3.json').write_text(json.dumps(proposal, indent=2, allow_nan=False) + '\n')
    report = {'runtime_s': runtime, 'membranes': [], 'overlays': []}
    for group in proposal['groups']:
        for membrane in group['membranes']:
            components = checks._coverage(project, args.project, group, membrane)
            report['membranes'].append({
                'label': membrane['label'], 'panel_label': membrane['panel_label'],
                'rotation': membrane['rotation'], 'chosen': membrane['row']['candidate_id'],
                'support': membrane['row']['evidence']['band_support'],
                'coverage': {'total': len(components), 'covered': sum(c for _, _, c in components),
                             'red': sum(r for _, r, _ in components),
                             'red_covered': sum(r and c for _, r, c in components)},
                'candidates': [{'id': r['candidate_id'], 'score': r['score'],
                                'exposure': r['selected_exposure_id'], 'y': r['band_row_y_rectified'],
                                'crop': r['crop_rect_rectified']} for r in membrane['row_candidates']]})
    report['overlays'] = checks._shots(project, args.project, proposal, args.prefix)
    checks._assert_format3(proposal)
    for group in proposal['groups']:
        checks._assert_coverage(project, args.project, group)
    result = json.dumps(report, indent=2, allow_nan=False)
    (output / f'{args.prefix}-audit.json').write_text(result + '\n')
    print(result)


if __name__ == '__main__':
    main()
