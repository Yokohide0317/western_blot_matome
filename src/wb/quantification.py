"""Independent fixed-area measurements of verified raw pixels."""
import hashlib
import json
import numpy as np
import tifffile
from pathlib import Path
from wb.model import QuantificationGroup

RAW16_REQUIRED = 'raw16元画像がないため定量できません。raw16 TIFFを取り込んでください。'


def find(items, key, value):
    item = next((i for i in items if getattr(i, key) == value), None)
    if item is None:
        raise ValueError(f"Unknown {key}")
    return item


def load(project, root, group):
    source = next((s for s in project.sources if s.source_asset_id == group.source_asset_id), None)
    if source is None or source.representation_kind != 'analysis_export_candidate' or not source.relative_path:
        raise ValueError(RAW16_REQUIRED)
    frame = find(project.frames, 'frame_id', group.frame_id)
    capture = find(project.captures, 'capture_id', group.capture_id)
    if not capture.analysis_source_asset_id:
        raise ValueError(RAW16_REQUIRED)
    if capture.analysis_source_asset_id != source.source_asset_id:
        raise ValueError('撮影と定量用画像の対応が変わっています')
    if frame.source_asset_id != source.source_asset_id or not source.relative_path:
        raise ValueError('定量用の元画像が見つかりません')
    if source.quant_eligibility == 'presentation_only':
        raise ValueError('表示用画像は定量できません')
    path = (Path(root) / source.relative_path).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('元画像の保存場所を確認してください')
    from wb.project import require_local
    require_local([path])
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(RAW16_REQUIRED) from exc
    except OSError as exc:
        raise ValueError('元画像を読み込めません。保存場所を確認してください') from exc
    if hashlib.sha256(data).hexdigest() != source.sha256 or len(data) != source.size_bytes:
        raise ValueError('元画像が変更されています。取り込みから確認してください')
    import io
    with tifffile.TiffFile(io.BytesIO(data)) as tif:
        index = frame.page_index
        if index is None:
            if len(tif.pages) != 1:
                raise ValueError('定量する画像のページを指定してください')
            index = 0
        if index < 0 or index >= len(tif.pages):
            raise ValueError('画像のページ指定を確認してください')
        if source.image and source.image.page_count is not None and len(tif.pages) != source.image.page_count:
            raise ValueError('元画像のページ数が変わっています')
        a = tif.pages[index].asarray()
    if a.ndim != 2 or a.dtype != np.dtype('uint16'):
        raise ValueError('定量には16-bitグレースケールの元画像が必要です')
    h, w = a.shape
    if source.image and (source.image.width != w or source.image.height != h or source.image.dtype != str(a.dtype)):
        raise ValueError('元画像の寸法または画素形式が変わっています')
    if (frame.width is not None and frame.width != w) or (frame.height is not None and frame.height != h):
        raise ValueError('元画像の寸法が変わっています')
    return a


def identity(project, g):
    p = find(project.panels, 'panel_id', g.panel_id)
    sample = next((s for s in project.sample_groups if s.sample_group_id == p.sample_group_id), None)
    count = p.display_geometry.lane_layout.lane_count if p.display_geometry else None
    if count is None:
        count = find(project.membrane_regions, 'region_id', p.region_id).lane_count
    if count is not None and [l.lane_id for l in g.lanes] != list(range(1, count + 1)):
        raise ValueError('レーン構成が変わっています。定量を作成し直してください')
    return [sample.samples[l.lane_id - 1].name if sample and l.lane_id <= len(sample.samples) else None for l in g.lanes]


def fingerprint(project, g):
    return hashlib.sha256(json.dumps(['1.0', g.model_dump(exclude={'results'}), identity(project, g), find(project.sources, 'source_asset_id', g.source_asset_id).sha256, find(project.frames, 'frame_id', g.frame_id).model_dump(), find(project.captures, 'capture_id', g.capture_id).model_dump()], sort_keys=True).encode()).hexdigest()


def validate(a, g):
    boxes = []
    for l in g.lanes:
        for x, y in ((l.x, l.y), (l.background_x, l.background_y)):
            if x < 0 or y < 0 or x + g.width_px > a.shape[1] or y + g.height_px > a.shape[0]:
                raise ValueError('ROI outside raw image')
        boxes.append(l)
    for bg in boxes:
        for band in boxes:
            if bg.background_x < band.x + g.width_px and band.x < bg.background_x + g.width_px and bg.background_y < band.y + g.height_px and band.y < bg.background_y + g.height_px:
                raise ValueError('Background overlaps band ROI')


def compute(project, root, g):
    a = load(project, root, g)
    validate(a, g)
    names = identity(project, g)
    threshold = g.saturation_value if g.saturation_value is not None else (np.iinfo(a.dtype).max if g.signal_polarity == 'high' else 0)
    if threshold < 0 or threshold > np.iinfo(a.dtype).max:
        raise ValueError('飽和値は0〜65535で指定してください')
    rows = []
    for l, name in zip(g.lanes, names):
        band = a[l.y:l.y+g.height_px, l.x:l.x+g.width_px].astype(np.float64)
        bg = a[l.background_y:l.background_y+g.height_px, l.background_x:l.background_x+g.width_px].astype(np.float64)
        raw, mean = float(band.sum()), float(bg.mean())
        saturated = int(((band >= threshold).sum() + (bg >= threshold).sum()) if g.signal_polarity == 'high' else ((band <= threshold).sum() + (bg <= threshold).sum()))
        status = 'excluded' if l.excluded else 'saturated' if saturated else 'ok'
        net = (raw - band.size * mean) * (1 if g.signal_polarity == 'high' else -1)
        rows.append(dict(lane_id=l.lane_id, sample_name=name, area_px=band.size, raw_sum=raw, background_mean=mean, net_signal=net if status == 'ok' else None, saturated_pixels=saturated, status=status, saturation_limit_verified=g.saturation_value is not None))
    g.results = dict(fingerprint=fingerprint(project, g), rows=rows, algorithm_version='1.0')


def current(project, root, g):
    width = height = 0
    try:
        a = load(project, root, g)
        height, width = a.shape
        validate(a, g)
        ok = bool(g.results and g.results['fingerprint'] == fingerprint(project, g))
        return dict(width=a.shape[1], height=a.shape[0], results_current=ok, **({} if ok else {'reason':'未計算または再計算が必要です'}))
    except (ValueError, OSError) as e:
        return dict(width=width, height=height, results_current=False, reason=str(e))


def excel(project, root, group):
    """Export numeric results and reproducible settings without spreadsheet formulas."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    if not current(project, root, group)['results_current']:
        raise ValueError('定量結果がありません。現在の条件で再計算してください')
    book = Workbook()
    results = book.active
    results.title = '定量結果'
    keys = ['lane_id', 'sample_name', 'area_px', 'raw_sum', 'background_mean', 'net_signal', 'saturated_pixels', 'status']
    results.append(['レーン', 'サンプル', '面積 (px)', '積分強度', '背景平均', '背景補正後', '飽和画素数', '状態', 'バンド x', 'バンド y', '背景 x', '背景 y', '幅 (px)', '高さ (px)'])
    for row, lane in zip(group.results['rows'], group.lanes):
        values = [row.get(key) for key in keys]
        values[7] = {'ok': '背景以下' if row['net_signal'] is not None and row['net_signal'] < 0 else '有効', 'excluded': '除外', 'saturated': '飽和'}.get(row['status'], row['status'])
        results.append(values + [lane.x, lane.y, lane.background_x, lane.background_y, group.width_px, group.height_px])
    results.auto_filter.ref = results.dimensions
    settings = book.create_sheet('解析条件')
    settings.append(['項目', '値'])
    source = find(project.sources, 'source_asset_id', group.source_asset_id)
    capture = find(project.captures, 'capture_id', group.capture_id)
    conditions = group.model_dump(exclude={'results', 'lanes'})
    conditions.update(source_sha256=source.sha256, exposure_s=capture.exposure_s,
                      algorithm_version=group.results.get('algorithm_version', '1.0'),
                      fingerprint=group.results['fingerprint'],
                      formula='raw_sum - area_px * background_mean' if group.signal_polarity == 'high' else 'area_px * background_mean - raw_sum',
                      saturation_limit_verified=group.saturation_value is not None,
                      effective_saturation_value=group.saturation_value if group.saturation_value is not None else (65535 if group.signal_polarity == 'high' else 0))
    for key, value in conditions.items():
        settings.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
    for sheet in book:
        sheet.freeze_panes = 'A2'
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'  # Preserve literal names such as =1+1, never execute them.
                if cell.row == 1:
                    cell.font = Font(bold=True, color='FFFFFF')
                    cell.fill = PatternFill('solid', fgColor='286B6C')
                elif isinstance(cell.value, float):
                    cell.number_format = '0.00'
                cell.alignment = Alignment(vertical='top')
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = min(48, max(14, max(len(str(c.value or '')) for c in col) + 2))
    output = BytesIO()
    book.save(output)
    return output.getvalue()
