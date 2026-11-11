"""Commands shared with the browser API."""

import argparse
import json
import subprocess
import sys
import zipfile

from wb import api, project as P


def parser():
    root = argparse.ArgumentParser(prog="wbm", description="Western Blot Matome: ウェスタンブロットの段を確認し、図を書き出します。")
    from wb import __version__

    root.add_argument("--version", action="version", version=f"wbm {__version__}")
    root.add_argument("--project", default=argparse.SUPPRESS, help="ZIP、画像フォルダ、.wblot")
    root.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSONで出力")
    sub = root.add_subparsers(dest="command", required=True)
    descriptions = {
        "init": ".wblotを作成",
        "open": "ブラウザを開く",
        "stop": "サーバーを停止（開いている全プロジェクト）",
        "recent": "最近開いたプロジェクトを表示",
        "status": "Selectionと採用画像を表示",
        "propose": "自動提案",
        "set-exposure": "採用露光を変更",
        "adjust": "角度と範囲を調整",
        "rotate": "選択範囲を保ったまま90度・180度回転",
        "rename": "段のラベルを変更",
        "reorder": "段を並べ替え",
        "include": "段を含める",
        "exclude": "段を除外",
        "duplicate": "段を複製",
        "add-membrane": "メンブレンと段を手動追加",
        "overview": "座標の目盛り付きで元画像全体を書き出す（膜の位置を読み取る・確かめる用）",
        "lanes": "レーン数を設定",
        "quantify": "raw16で定量（バンド選択から枠を配置）",
        "marker": "マーカーを左右にトリミング・移動（縦はバンドの範囲と共通）",
        "export": "PPTXかPNGを書き出す",
        "validate": "元画像と編集内容を検証",
        "install-skill": "Skillを導入",
        "install-app": "常駐アプリをApplications / Programsに作成",
        "app": "WBMアプリを開き、共通サーバーを起動",
        "config": "設定ファイル（ChemiDocのファイル名の読み方など）を表示・作成・確認",
        "update": "新しいバージョンに更新（--check で確認だけ）",
        "start": "サーバーを起動してURLを表示",
    }
    for name, description in descriptions.items():
        command = sub.add_parser(name, help=description, description=description)
        command.add_argument("--project", default=argparse.SUPPRESS, help="ZIP、画像フォルダ、.wblot")
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSONで出力")
        if name in ("init", "open"):
            command.add_argument("path", nargs="?" if name == "open" else None)
        if name == 'init':
            command.add_argument('--refresh', action='store_true', help='既存状態をバックアップし、元画像を取り込み直して編集を初期化')
        if name in ("open", "app"):
            command.add_argument("--no-browser", action="store_true")
        if name == 'install-app':
            command.add_argument('--destination', help='作成するWBM.app / WBM.exeのパス')
        if name == 'start':
            command.add_argument('--desktop-session', help=argparse.SUPPRESS)
            command.add_argument('--owner-pid', type=int, help=argparse.SUPPRESS)
        if name == "propose":
            command.add_argument("--apply", action="store_true")
            command.add_argument("--summary", action="store_true", help="提案の段と警告件数だけを表示")
        if name in ("set-exposure", "adjust", "rename", "marker", "rotate"):
            command.add_argument("panel")
        if name == 'rotate':
            command.add_argument('--degrees', type=int, choices=(-90, 90, 180), default=90)
        if name in ("include", "exclude", "duplicate"):
            command.add_argument("panel")
        if name == "add-membrane":
            command.add_argument("x0", type=float)
            command.add_argument("y0", type=float)
            command.add_argument("x1", type=float)
            command.add_argument("y1", type=float)
            command.add_argument("--rotation", type=float, default=0.0)
            command.add_argument("--membrane-label")
            command.add_argument("--label")
            command.add_argument("--frame", help="座標の基準にする画像（既定は wbm overview と同じ基準画像）")
        if name == "overview":
            command.add_argument("--frame", help="表示する画像のframe_id（既定は膜の座標の基準画像）")
            command.add_argument("--grid", type=int, help="目盛りの間隔（px、既定は画像の大きさから自動）")
            command.add_argument("--raw", action="store_true", help="明るさを引き伸ばさずに表示")
        if name == "reorder":
            command.add_argument("panels", nargs="+")
        if name == "lanes":
            command.add_argument("count", type=int)
            command.add_argument("--panel")
            command.add_argument("--samples")
        if name == "quantify":
            actions = command.add_subparsers(dest='quant_action', required=True)
            for action, help_text in {
                'start': '段のバンド選択から定量枠を作成',
                'show': '定量グループと結果を表示（ID省略で一覧）',
                'place': '現在のバンド選択から枠を再配置（結果を消去）',
                'update': 'JSONファイルで枠や計算設定を変更',
                'compute': '保存した枠で計算',
                'export': '計算済みの結果をExcel・CSVに出力',
            }.items():
                q = actions.add_parser(action, help=help_text, description=help_text)
                q.add_argument('target', nargs='?' if action in ('show', 'export') else None,
                               help='段のラベルまたはID' if action == 'start' else '定量グループID')
                q.add_argument('--project', default=argparse.SUPPRESS)
                q.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
                if action == 'start':
                    q.add_argument('--capture', help='定量に使う撮影ID（省略時は段の採用露光）')
                if action == 'update':
                    q.add_argument('--settings', required=True, help='update_quantificationの変更内容を記したJSONファイル')
                if action == 'export':
                    q.add_argument('--format', choices=('excel', 'csv'), default='excel')
                    q.add_argument('--all', action='store_true', help='全定量グループを別シートにまとめてExcel出力')
        if name == "set-exposure":
            command.add_argument("capture_id")
        if name == "rename":
            command.add_argument("label")
        if name == "adjust":
            command.add_argument("--rotation", type=float)
            command.add_argument("--crop-left", type=float)
            command.add_argument("--crop-right", type=float)
            command.add_argument("--crop-top", type=float)
            command.add_argument("--crop-bottom", type=float)
            command.add_argument("--width", type=int)
        if name == "marker":
            command.add_argument("--left", type=float, help="左端（傾き補正後の座標）")
            command.add_argument("--right", type=float, help="右端")
            command.add_argument("--shift", type=float, help="左右に移動する量（右が正）")
            command.add_argument("--reset", action="store_true", help="自動のマーカー範囲に戻す")
        if name == "export":
            command.add_argument("kind", choices=("pptx", "png"))
            command.add_argument("--no-merge", action="store_true",
                                 help="PPTXの2枚目に入れる全体画像（発光＋マーカーの重ね合わせ）を省く")
        if name == "config":
            command.add_argument("--init", action="store_true", help="説明つきの設定ファイルを作る（既にあれば変更しない）")
            command.add_argument("--check", metavar="ZIPまたはフォルダ", help="今の設定でファイル名がどう読まれるかを表示")
        if name == "update":
            command.add_argument("--check", action="store_true", help="GitHubで最新版を確認して表示するだけ")
        if name == "install-skill":
            command.add_argument("--claude", action="store_true")
            command.add_argument("--codex", action="store_true")
            command.add_argument("--agents", action="store_true", help="~/.agents/skills に入れる")
    return root


def _execute(args):
    name = args.command
    if name == "install-skill":
        return api.install_skill(args.claude, args.codex, args.agents)
    if name == 'install-app':
        from wb.desktop import install_app
        return install_app(args.destination)
    if name == 'app':
        from wb.desktop import start_app
        return start_app(no_browser=args.no_browser)
    if name == "update":
        from wb import update

        return update.status(force=True) if args.check else update.update_now()
    if name == "start":
        from wb import server

        desktop = {'session': args.desktop_session, 'owner_pid': args.owner_pid} if args.desktop_session else None
        if desktop:
            return server.ensure_server(desktop=desktop)
        from wb.desktop import start_app
        return start_app(no_browser=True)
    if name == "config":
        from wb import config

        if args.init:
            return config.init()
        if args.check:
            return config.check(args.check)
        return config.show()
    target = getattr(args, "project", None)
    if name == "init":
        return api.init_project(args.path or target, args.refresh)
    if name == "stop":
        return api.stop_project()
    if name == "recent":
        return api.recent_projects()
    if name == "open":
        return api.open_project(args.path or target, args.no_browser)
    path = P.locate(target)
    if name == 'quantify':
        return _quantify(path, args)
    if name == "status":
        return api.status(path)
    if name == "validate":
        return api.validate(path)
    if name == "propose":
        project = api._load(path)
        proposal = api.propose(path)
        if args.apply:
            saved = api.apply_proposal(path, proposal, project.project_revision)
            result = {
                "ok": True,
                "project_revision": saved.project_revision,
                "panels": api.panel_summaries(saved),
                "excluded_image_sets": proposal.get("excluded_image_sets", []),
                "warnings_count": _warnings_count(proposal),
            }
            if not result["panels"]:
                result["next"] = ("膜を自動で見つけられませんでした。wbm overview で座標の目盛り付きの画像を見て、"
                                  "膜ごとに wbm add-membrane で追加してください")
            return result
        if args.summary:
            return {
                "panels": [
                    {"label": m["panel_label"], "membrane_label": m["label"],
                     "selected_band_row_candidate_id": m["row"]["candidate_id"],
                     "selected_exposure_id": m["row"]["selected_exposure_id"]}
                    for group in proposal["groups"] for m in group["membranes"]
                ],
                "excluded_image_sets": proposal.get("excluded_image_sets", []),
                "warnings_count": _warnings_count(proposal),
            }
        return proposal
    if name == "export":
        return api.export_project(path, args.kind, merge=not args.no_merge)
    if name == "overview":
        return api.guide_image(path, args.frame, args.grid, stretch=not args.raw)
    project = api._load(path)
    revision = project.project_revision
    if name == "reorder":
        values = [v.strip() for arg in args.panels for v in arg.split(",") if v.strip()]
        ids = [api.resolve_panel(project, value).panel_id for value in values]
        saved = api.reorder_panels(path, ids, revision)
        return {"ok": True, "project_revision": saved.project_revision, "panels": api.panel_summaries(saved)}
    if name == "add-membrane":
        saved = api.add_manual_membrane(
            path,
            revision,
            rect={"x0": args.x0, "y0": args.y0, "x1": args.x1, "y1": args.y1},
            rotation_deg=args.rotation,
            user_label=args.membrane_label,
            label=args.label,
            reference_frame_id=args.frame,
        )
        panel = saved.panels[-1]
        entry = next(p for p in api.panel_summaries(saved) if p["panel_id"] == panel.panel_id)
        return {"ok": True, "project_revision": saved.project_revision, "panel": entry, "new_panel_id": panel.panel_id}
    if name == "lanes":
        pid = api.resolve_panel(project, args.panel).panel_id if args.panel else None
        samples = args.samples.split(",") if args.samples is not None else None
        saved = api.set_lanes(path, args.count, revision, panel_id=pid, sample_names=samples)
        return {"ok": True, "project_revision": saved.project_revision, "panels": api.panel_summaries(saved)}
    panel = api.resolve_panel(project, args.panel)
    if name in ("include", "exclude"):
        saved = api.set_panel_included(path, panel.panel_id, name == "include", revision)
    elif name == "duplicate":
        saved = api.duplicate_panel(path, panel.panel_id, revision)
        old_ids = {p.panel_id for p in project.panels}
        panel = next(p for p in saved.panels if p.panel_id not in old_ids)
    elif name == "set-exposure":
        saved = api.set_selected_exposure(path, panel.panel_id, args.capture_id, revision)
    elif name == "rename":
        saved = api.rename_panel(path, panel.panel_id, args.label, revision)
    elif name == 'rotate':
        saved = api.rotate_panel(path, panel.panel_id, args.degrees, revision)
    elif name == "marker":
        x0, x1 = (None, None) if args.reset else _marker_x(project, panel, args)
        saved = api.set_marker_x(path, panel.panel_id, x0, x1, revision)
    elif name == "adjust":
        saved = api.adjust(
            path,
            panel.panel_id,
            revision,
            args.rotation,
            args.crop_top,
            args.crop_bottom,
            args.width,
            args.crop_left,
            args.crop_right,
        )
    else:
        raise ValueError(f"不明なコマンド: {name}")
    entry = next(p for p in api.panel_summaries(saved) if p["panel_id"] == panel.panel_id)
    receipt = {"ok": True, "project_revision": saved.project_revision, "panel": entry}
    if name == "duplicate":
        receipt["new_panel_id"] = panel.panel_id
    return receipt


def _quantify(path, args):
    from pathlib import Path
    from uuid import uuid4
    from wb import quantification as Q
    project = api._load(path)
    revision = project.project_revision
    action = args.quant_action
    if action == 'export':
        if args.all:
            if args.target is not None or args.format != 'excel':
                raise ValueError('--allはグループIDを指定せず、Excel形式で使用してください')
            data = Q.excel_all(project, path.parent)
            receipt = {'group_count': len(project.quantification_groups)}
        else:
            if not args.target:
                raise ValueError('定量グループIDか--allを指定してください')
            group = Q.find(project.quantification_groups, 'group_id', args.target)
            data = Q.excel(project, path.parent, group) if args.format == 'excel' else Q.csv_bytes(project, path.parent, group)
            receipt = {'group_id': group.group_id}
        suffix = 'xlsx' if args.format == 'excel' else 'csv'
        output = P.output_dir(path) / 'exports' / f'quantification-{uuid4().hex[:12]}.{suffix}'
        with output.open('xb') as stream:
            stream.write(data)
        return {'path': str(output), **receipt}
    if action == 'show' and args.target is None:
        return {'project_revision': revision, 'groups': [
            {'group_id': g.group_id, 'panel_id': g.panel_id, 'capture_id': g.capture_id,
             'placement_note': getattr(g, 'placement_note', None),
             **Q.current(project, path.parent, g)} for g in project.quantification_groups]}
    if action == 'start':
        panel = api.resolve_panel(project, args.target)
        project = api.start_quantification(path, panel.panel_id, revision, capture_id=args.capture)
        group = project.quantification_groups[-1]
    else:
        group = Q.find(project.quantification_groups, 'group_id', args.target)
        if action == 'show':
            return {'project_revision': revision, 'group': group.model_dump(mode='json'),
                    **Q.current(project, path.parent, group)}
        if action == 'place':
            project = api.place_quantification(path, group.group_id, revision)
        elif action == 'update':
            changes = json.loads(Path(args.settings).read_text(encoding='utf-8'))
            allowed = {'width_px', 'height_px', 'signal_polarity', 'saturation_value', 'lanes'}
            if not isinstance(changes, dict) or changes.keys() - allowed or not changes:
                raise ValueError('設定JSONには枠・レーン・信号の向き・飽和しきい値の変更を指定してください')
            project = api.update_quantification(path, group.group_id, revision, **changes)
        elif action == 'compute':
            project = api.compute_quantification(path, group.group_id, revision)
        group = Q.find(project.quantification_groups, 'group_id', group.group_id)
    return {'ok': True, 'project_revision': project.project_revision, 'group': group.model_dump(mode='json')}


def _marker_x(project, panel, args):
    from wb.rendering import marker_rect

    if all(v is None for v in (args.left, args.right, args.shift)):
        raise ValueError("--left/--right/--shift か --reset を指定してください。縦の範囲は adjust の --crop-top/--crop-bottom で変えます")
    current = marker_rect(project, panel)
    if current is None:
        raise ValueError("この段にはColorimetricマーカーの位置合わせがありません")
    x0 = current.x0 if args.left is None else args.left
    x1 = current.x1 if args.right is None else args.right
    shift = args.shift or 0.0
    return x0 + shift, x1 + shift


def _warnings_count(proposal):
    return len(proposal.get("warnings", [])) + sum(len(g.get("warnings", [])) for g in proposal["groups"])


def _mutation_message(result):
    revision = result["project_revision"]
    if 'group' in result:
        group = result['group']
        return f"定量 {group['group_id']} を更新しました（版 {revision}）。" + (group.get('placement_note') or '')
    if "panel" in result:
        panel = result["panel"]
        return f"段 {panel['label']} を更新しました（版 {revision}）。"
    if "warnings_count" in result:
        return f"提案を反映しました。段 {len(result['panels'])} 件、警告 {result['warnings_count']} 件（版 {revision}）。"
    return f"段 {len(result['panels'])} 件を更新しました（版 {revision}）。"


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):  # Windows consoles and pipes default to a legacy code page.
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    json_output = getattr(args, "json", False)
    try:
        result = _execute(args)
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        if json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif isinstance(result, dict) and result.get("ok"):
            print(_mutation_message(result))
        elif args.command == "propose":
            count = len(result["panels"]) if "panels" in result else sum(len(g["membranes"]) for g in result["groups"])
            warnings = result["warnings_count"] if "warnings_count" in result else _warnings_count(result)
            print(f"段 {count} 件を提案しました。警告 {warnings} 件。")
        elif args.command == "open":
            print(result["url"])
        elif args.command == "recent":
            for entry in result["projects"]:
                print(f"{entry['last_opened_local']}  {entry['path']}{'' if entry['exists'] else '  (見つかりません)'}")
        elif args.command == "config" and "files" in result:
            for f in result["files"]:
                role = {"chemiluminescence": "発光", "colorimetric": "マーカー"}.get(f["modality"], "読めない")
                exposure = f"{f['exposure_s']:g}s" if f["exposure_s"] is not None else "-"
                print(f"{role:<5} {exposure:>8}  {f['kind']:<5}  {f['file']}")
            print(f"画像 {result['images']} 件: 発光 {result['signal']} / マーカー {result['marker']} / 読めない {len(result['unrecognised'])}（設定: {result['config']}）")
        elif args.command == "config" and "naming" in result:
            print(f"設定ファイル: {result['path']}{'' if result['exists'] else '（未作成。既定値を使用中。wbm config --init で作成）'}")
            for section in ("naming", "update"):
                print(f"[{section}]")
                for key, value in result[section].items():
                    print(f"  {key} = {value!r}")
        elif args.command == "config":
            print(f"{'作成しました' if result['created'] else '既にあります'}: {result['path']}")
        elif args.command == "install-skill":
            print("\n".join(result["paths"]))
        elif args.command == "update":
            print(result["message"])
            if result.get("available") and not result.get("can_apply"):
                print(f"更新するには次の1行を実行してください: {result['command']}")
        elif args.command in ("start", "app"):
            print(result["url"])
        elif isinstance(result, dict) and "path" in result:
            print(result["path"])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.command == "validate" and result["source_issues"] else 0
    except (ValueError, OSError, KeyError, StopIteration, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        message = f"処理できませんでした: {exc}"
        if json_output:
            print(json.dumps({"error": message}, ensure_ascii=False))
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
