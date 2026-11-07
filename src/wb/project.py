"""Load, save, migrate and update project.json.

State helpers are pure: they take a Project and return a new one. Nothing here
bumps project_revision except save_project.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from wb.model import (
    Annotation,
    Capture,
    DisplayGeometry,
    FlagTargetKind,
    Frame,
    HistoryEntry,
    ImageSet,
    InputArchive,
    MembraneRegion,
    Panel,
    Project,
    ReviewFlag,
    Source,
    UnassignedTargetRule,
)

SCHEMA_VERSION = "1.0"
DRAFT_SCHEMA_VERSION = "1.0-draft"


class ProjectError(ValueError):
    pass


class UnsupportedSchemaVersion(ProjectError):
    pass


class RevisionConflict(ProjectError):
    pass


class UnknownId(ProjectError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _history(operation: str, revision: int, summary: str = "") -> HistoryEntry:
    return HistoryEntry(operation=operation, project_revision=revision, timestamp=_now(), summary=summary)


def _revalidate(project: Project) -> Project:
    return Project.model_validate(project.model_dump())


def _find(items: Iterable[Any], attr: str, value: str, what: str) -> Any:
    for item in items:
        if getattr(item, attr) == value:
            return item
    raise UnknownId(f"{what}が見つかりません: {value!r}")


# ---------------------------------------------------------------- stable ids


def stable_id(prefix: str, *parts: Any) -> str:
    """Same parts, same id: retrying an operation cannot mint a second record."""
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(blob.encode()).hexdigest()[:12]}"


def region_id_for(user_label: str) -> str:
    # keyed by the user's label (I, J, K, L), never by the antibody: K and L are both HA
    return f"REGION_{user_label}"


def panel_id_for(user_label: str) -> str:
    return f"PANEL_{user_label}"


def mint_annotation_id(data: dict[str, Any]) -> str:
    keys = ("panel_id", "frame_id", "lane_id", "purpose", "geometry", "created_by", "method")
    return stable_id("ANN", *(data.get(k) for k in keys))


# ----------------------------------------------------------- load / migrate


def migrate_1_0_draft(data: dict[str, Any]) -> dict[str, Any]:
    """1.0-draft (the seed) -> 1.0. The untouched draft is kept in `migrated_from`."""
    original = copy.deepcopy(data)
    out = copy.deepcopy(data)
    out["schema_version"] = SCHEMA_VERSION
    for source in out.get("sources", []):
        source.setdefault("relative_path", None)
    out.setdefault("marker_registrations", [])
    out.setdefault("sample_groups", [])
    out.setdefault("outputs", [])
    out["migrated_from"] = {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "migrated_at": _now(),
        "original": original,
    }
    entry = _history("migrate_1_0_draft", out.get("project_revision", 1), f"from {DRAFT_SCHEMA_VERSION}")
    out["history"] = [*out.get("history", []), entry.model_dump()]
    return out


def locate(path=None, *, create=False) -> Path:
    """Resolve a ZIP, image folder or .wblot to its project file.

    With create=True a ZIP is safely extracted once; a folder's default filename
    may be returned before it exists. No legacy project.json is guessed.
    """
    target = Path.cwd() if path is None else Path(path).expanduser().absolute()
    if target.suffix.lower() == ".zip":
        from wb.ingest import archive_root, materialize_archive

        target = materialize_archive(target) if create else archive_root(target)
    if target.suffix == ".wblot":
        if not create and not target.is_file():
            raise ProjectError(f".wblotが見つかりません: {target}")
        return target
    if not target.is_dir():
        raise ProjectError(f"画像フォルダが見つかりません: {target}")
    files = sorted(target.glob("*.wblot"))
    if len(files) > 1:
        raise ProjectError(f".wblotが複数あります。--projectで指定してください: {target}")
    if files:
        return files[0]
    if create:
        return target / (target.name + ".wblot")
    raise ProjectError(f".wblotがありません。wbm initを実行してください: {target}")


def output_dir(path) -> Path:
    """``wblot_output`` beside the images: exports and backups the user keeps."""
    target = Path(path)
    root = target if target.is_dir() else locate(target).parent
    output = root / "wblot_output"
    (output / "exports").mkdir(parents=True, exist_ok=True)
    return output


def _platform_dir(kind: str) -> Path:
    """Per-user folder for ``state`` or ``cache`` on macOS, Windows and Linux."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / ("Library/Application Support/WBM" if kind == "state" else "Library/Caches/WBM")
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "WBM"
        return local if kind == "state" else local / "Cache"
    variable, fallback = ("XDG_STATE_HOME", ".local/state") if kind == "state" else ("XDG_CACHE_HOME", ".cache")
    return Path(os.environ.get(variable) or home / fallback) / "wbm"


def _dir(env: str, default: Path) -> Path:
    return Path(os.environ[env]).expanduser() if os.environ.get(env) else default


def app_home() -> Path:
    """Per-user state shared by every project: the server and the recent-projects list."""
    home = _dir("WBM_HOME", _platform_dir("state"))
    home.mkdir(parents=True, exist_ok=True)
    return home


def library_dir() -> Path:
    """Where ZIPs dropped on the top page are kept (their original location is unknown)."""
    library = _dir("WBM_LIBRARY", Path.home() / "Documents" / "western_blot_matome")
    library.mkdir(parents=True, exist_ok=True)
    return library


def project_key(path) -> str:
    """Short stable ID of an image folder, from its absolute location."""
    target = Path(path).expanduser().absolute()
    root = target if target.is_dir() else target.parent
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:10]


def cache_dir(path) -> Path:
    """Regenerable renders, kept out of the data folder (which may sync to iCloud)."""
    base = _dir("WBM_CACHE", _platform_dir("cache")) / project_key(path)
    for name in ("cache", "panels", "overviews"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return base


def load_project(path: str | Path) -> Project:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if version == DRAFT_SCHEMA_VERSION:
        data = migrate_1_0_draft(data)
    elif version != SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(f"cannot read schema_version {version!r}")
    return Project.model_validate(data)


# --------------------------------------------------------------------- save


def save_project(
    project: Project,
    path: str | Path,
    expected_revision: int,
    operation: str = "save",
    summary: str = "",
) -> Project:
    """Write atomically and return the saved project (revision + 1, history appended).

    Conflict detection is check-then-replace, not a lock: it protects against a
    stale editor, not against two writers racing within the same instant.
    """
    path = Path(path)
    if path.exists():
        on_disk = json.loads(path.read_text(encoding="utf-8")).get("project_revision")
        if on_disk != expected_revision:
            raise RevisionConflict(f"{path}の現在の版は{on_disk}です。指定された版は{expected_revision}です")
    saved = project.model_copy(deep=True)
    saved.project_revision = project.project_revision + 1
    saved.history.append(_history(operation, saved.project_revision, summary))
    text = json.dumps(saved.model_dump(mode="json"), ensure_ascii=False, indent=2)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return saved


# ------------------------------------------------------------- construction


def import_records(project: Project, records: dict[str, Any]) -> Project:
    """Add ingest records by id. Re-importing the same records changes nothing.

    Existing frames/captures/image_sets are kept as they are so a re-import never
    overwrites a human's review. A source whose sha256 differs from the stored one
    is replaced and its dependent geometry is flagged for review.
    """
    out = project.model_copy(deep=True)
    if records.get("input_archive") is not None:
        out.input_archive = InputArchive.model_validate(records["input_archive"])

    index = {s.source_asset_id: i for i, s in enumerate(out.sources)}
    changed: list[SourceIssue] = []
    for raw in records.get("sources", []):
        new = Source.model_validate(raw)
        i = index.get(new.source_asset_id)
        if i is None:
            index[new.source_asset_id] = len(out.sources)
            out.sources.append(new)
        elif out.sources[i].sha256 != new.sha256:
            changed.append(SourceIssue(new.source_asset_id, "hash_changed", out.sources[i].sha256, new.sha256))
            out.sources[i] = new
    for key, cls, attr in (
        ("frames", Frame, "frame_id"),
        ("captures", Capture, "capture_id"),
        ("image_sets", ImageSet, "image_set_id"),
    ):
        items = getattr(out, key)
        have = {getattr(x, attr) for x in items}
        for raw in records.get(key, []):
            item = cls.model_validate(raw)
            if getattr(item, attr) not in have:
                have.add(getattr(item, attr))
                items.append(item)
    return flag_dependents(_revalidate(out), changed)


def new_project(project_id: str, records: dict[str, Any], seed: dict[str, Any] | None = None) -> Project:
    """Build a project from ingest records.

    From the seed only the user's label mapping is carried over (regions, panels,
    Actin rules, unresolved items). Coordinates, lane counts, image sets and
    exposure choices start empty, whatever the seed contains.
    """
    project = Project(project_id=project_id, history=[_history("new_project", 1, project_id)])
    if seed is not None:
        project.membrane_regions = [
            MembraneRegion(
                region_id=r["region_id"],
                user_label=r["user_label"],
                target_label=r.get("target_label"),
                target_assignment_source=r.get("target_assignment_source"),
                target_assignment_status=r.get("target_assignment_status", "unresolved"),
            )
            for r in seed.get("membrane_regions", [])
        ]
        project.panels = [
            Panel(
                panel_id=p["panel_id"],
                region_id=p["region_id"],
                label=p.get("label"),
                display_order=p.get("display_order"),
            )
            for p in seed.get("panels", [])
        ]
        project.unassigned_target_rules = [
            UnassignedTargetRule.model_validate(r) for r in seed.get("unassigned_target_rules", [])
        ]
        project.unresolved_items = list(seed.get("unresolved_items", []))
    return import_records(_revalidate(project), records)


def add_region(project: Project, user_label: str, target_label: str | None = None) -> Project:
    """Add a region and its panel for a user label; adding it again is a no-op."""
    rid, pid = region_id_for(user_label), panel_id_for(user_label)
    out = project.model_copy(deep=True)
    if any(r.region_id == rid for r in out.membrane_regions):
        return out
    out.membrane_regions.append(MembraneRegion(region_id=rid, user_label=user_label, target_label=target_label))
    out.panels.append(
        Panel(
            panel_id=pid,
            region_id=rid,
            label=target_label,
            display_order=len(out.panels) + 1,
        )
    )
    return out


def select_band_row(project: Project, panel_id: str, candidate_id: str) -> Project:
    out = project.model_copy(deep=True)
    panel = _find(out.panels, "panel_id", panel_id, "panel")
    region = _find(out.membrane_regions, "region_id", panel.region_id, "region")
    candidate = _find(region.band_row_candidates, "candidate_id", candidate_id, "band row candidate")
    dg = panel.display_geometry
    if not dg or not dg.crop_rect:
        raise ValueError("段を切り替える前に表示範囲を指定してください")
    dg.crop_rect.y0 = candidate.crop_rect_rectified.y0
    dg.crop_rect.y1 = candidate.crop_rect_rectified.y1
    dg.geometry_revision += 1
    panel.selected_band_row_candidate_id = candidate_id
    panel.selected_exposure_id = candidate.selected_exposure_id
    panel.exposure_reason = candidate.exposure_reason
    panel.review_status = "proposed"
    image_set = next(
        (
            s
            for s in out.image_sets
            if s.image_set_id == panel.image_set_id and candidate.selected_exposure_id in s.capture_ids
        ),
        None,
    )
    if image_set is None:
        image_set = next((s for s in out.image_sets if candidate.selected_exposure_id in s.capture_ids), None)
    if image_set is None:
        raise ValueError("候補の露光は撮影系列に含まれる必要があります")
    panel.image_set_id = image_set.image_set_id
    return _revalidate(out)


# ------------------------------------------------------------- source checks


@dataclass(frozen=True)
class SourceIssue:
    source_asset_id: str
    kind: Literal["missing", "hash_changed", "size_changed"]
    expected: str | None = None
    actual: str | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_SF_DATALESS = 0x40000000  # macOS: the file's contents live only in iCloud Drive


def is_cloud_only(path) -> bool:
    """True for an iCloud Drive file not downloaded to this Mac (reading it can stall for minutes)."""
    try:
        return sys.platform == "darwin" and bool(getattr(os.stat(path), "st_flags", 0) & _SF_DATALESS)
    except OSError:
        return False


def require_local(paths) -> None:
    """Stop with a clear message instead of stalling on files that are only in iCloud Drive."""
    cloud = [Path(p) for p in paths if is_cloud_only(p)]
    if cloud:
        more = f" ほか{len(cloud) - 1}件" if len(cloud) > 1 else ""
        raise ValueError(
            f"iCloud Driveにだけあり、このMacにダウンロードされていないファイルがあります: {cloud[0].name}{more}。"
            f"Finderで「{cloud[0].parent}」を右クリックし「今すぐダウンロード」を選んでから、もう一度開いてください。"
        )


def verify_sources(project: Project, project_dir: str | Path) -> list[SourceIssue]:
    """Report sources whose file is missing or differs from the recorded values.

    Sources without a relative_path are skipped. Image width/height are not read
    from the file: a different size_bytes or sha256 already covers a changed image,
    and this module has no TIFF reader.
    """
    require_local(Path(project_dir) / s.relative_path for s in project.sources if s.relative_path)
    issues = []
    for s in project.sources:
        if s.relative_path is None:
            continue
        p = Path(project_dir) / s.relative_path
        if not p.is_file():
            issues.append(SourceIssue(s.source_asset_id, "missing", s.relative_path))
        elif p.stat().st_size != s.size_bytes:
            issues.append(SourceIssue(s.source_asset_id, "size_changed", str(s.size_bytes), str(p.stat().st_size)))
        elif (digest := _sha256(p)) != s.sha256:
            issues.append(SourceIssue(s.source_asset_id, "hash_changed", s.sha256, digest))
    return issues


def relink_by_hash(project: Project, search_dir: str | Path) -> Project:
    """Point sources at files under search_dir with the same sha256.

    Paths are stored relative to search_dir, which must be the project directory.
    A source is only changed when a file with its exact hash is found.
    """
    root = Path(search_dir)
    by_size: dict[int, list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            by_size.setdefault(p.stat().st_size, []).append(p)
    out = project.model_copy(deep=True)
    for s in out.sources:
        for p in by_size.get(s.size_bytes, []):
            if _sha256(p) == s.sha256:
                s.relative_path = p.relative_to(root).as_posix()
                break
    return out


_REASON = {"hash_changed": "source_hash_changed", "size_changed": "source_size_changed"}


def flag_dependents(project: Project, issues: Iterable[SourceIssue]) -> Project:
    """Flag geometry that was drawn on a source whose content changed.

    Flags are listed in project.review_flags; coordinates are kept, not deleted,
    but must not be used until a person re-confirms them (see clear_review_flags).
    A missing file is not a change and flags nothing.
    """
    changed = {i.source_asset_id: i for i in issues if i.kind in _REASON}
    out = project.model_copy(deep=True)
    if not changed:
        return out
    frame_source = {f.frame_id: f.source_asset_id for f in out.frames}
    capture_sources = {
        c.capture_id: {
            c.presentation_source_asset_id,
            c.native_source_asset_id,
            c.analysis_source_asset_id,
        }
        for c in out.captures
    }

    def sources_of(*frame_ids: str | None) -> set[str]:
        return {frame_source[f] for f in frame_ids if f in frame_source} & changed.keys()

    targets: list[tuple[FlagTargetKind, str, set[str]]] = []
    for a in out.annotations:
        targets.append(("annotation", a.annotation_id, sources_of(a.frame_id)))
    for p in out.panels:
        dg = p.display_geometry
        hit = sources_of(dg.reference_frame_id if dg else None)
        hit |= capture_sources.get(p.selected_exposure_id, set()) & changed.keys()
        targets.append(("panel", p.panel_id, hit))
    for t in out.transforms:
        targets.append(("transform", t.transform_id, sources_of(t.from_frame_id, t.to_frame_id)))
    for r in out.membrane_regions:
        targets.append(("region", r.region_id, sources_of(r.reference_frame_id, r.geometry and r.geometry.frame_id)))
    for s in out.image_sets:
        targets.append(("image_set", s.image_set_id, sources_of(s.reference_frame_id)))
    for m in out.marker_registrations:
        targets.append(
            ("marker_registration", m.marker_registration_id, sources_of(m.marker_roi and m.marker_roi.frame_id))
        )

    have = {(f.target_kind, f.target_id, f.source_asset_id) for f in out.review_flags}
    for kind, target_id, source_ids in targets:
        for source_id in sorted(source_ids):
            if (kind, target_id, source_id) not in have:
                issue = changed[source_id]
                out.review_flags.append(
                    ReviewFlag(
                        target_kind=kind,
                        target_id=target_id,
                        source_asset_id=source_id,
                        reason=_REASON[issue.kind],
                        detail=f"{issue.expected} -> {issue.actual}",
                    )
                )
    return out


def needs_review(project: Project, target_kind: FlagTargetKind, target_id: str) -> bool:
    return any(f.target_kind == target_kind and f.target_id == target_id for f in project.review_flags)


def clear_review_flags(project: Project, target_kind: FlagTargetKind, target_id: str) -> Project:
    out = project.model_copy(deep=True)
    out.review_flags = [f for f in out.review_flags if not (f.target_kind == target_kind and f.target_id == target_id)]
    return out


# ------------------------------------------------------- state transitions


def set_selected_exposure(project: Project, panel_id: str, capture_id: str | None) -> Project:
    """Choose the exposure used for output. Touches only this panel."""
    out = project.model_copy(deep=True)
    panel = _find(out.panels, "panel_id", panel_id, "panel")
    if capture_id is not None:
        _find(out.captures, "capture_id", capture_id, "capture")
        region = _find(out.membrane_regions, "region_id", panel.region_id, "region")
        if panel.image_set_id or region.image_set_id:
            image_set = _find(out.image_sets, "image_set_id", panel.image_set_id or region.image_set_id, "image_set")
            if capture_id not in image_set.capture_ids:
                raise ProjectError(f"{capture_id} is not in image set {image_set.image_set_id}")
    panel.selected_exposure_id = capture_id
    return out


def set_display_geometry(project: Project, panel_id: str, geometry: DisplayGeometry) -> Project:
    """Replace a panel's display geometry; geometry_revision is set by this function."""
    out = project.model_copy(deep=True)
    panel = _find(out.panels, "panel_id", panel_id, "panel")
    _find(out.frames, "frame_id", geometry.reference_frame_id, "frame")
    previous = panel.display_geometry.geometry_revision if panel.display_geometry else 0
    panel.display_geometry = geometry.model_copy(update={"geometry_revision": previous + 1})
    return clear_review_flags(out, "panel", panel_id)


def add_annotation(project: Project, annotation: Annotation | dict[str, Any]) -> Project:
    """Add a draft/proposed annotation; confirm_annotation is the only way to confirm.

    A dict without annotation_id gets one from mint_annotation_id, so adding the
    same annotation twice is a no-op.
    """
    data = annotation.model_dump() if isinstance(annotation, Annotation) else dict(annotation)
    data.setdefault("annotation_id", mint_annotation_id(data))
    new = Annotation.model_validate(data)
    if new.review_status not in ("draft", "proposed"):
        raise ProjectError("新しい注釈はdraftかproposedで作成してください")
    out = project.model_copy(deep=True)
    for existing in out.annotations:
        if existing.annotation_id == new.annotation_id:
            if existing == new:
                return out
            raise ProjectError(f"annotation {new.annotation_id} already exists with other content")
    panel = _find(out.panels, "panel_id", new.panel_id, "panel")
    _find(out.frames, "frame_id", new.frame_id, "frame")
    out.annotations.append(new)
    ids = panel.background_roi_ids if new.purpose == "background_roi" else panel.band_annotation_ids
    ids.append(new.annotation_id)
    return out


def confirm_annotation(project: Project, annotation_id: str) -> Project:
    """draft/proposed -> confirmed. Also how a person re-accepts a flagged annotation."""
    out = project.model_copy(deep=True)
    ann = _find(out.annotations, "annotation_id", annotation_id, "annotation")
    if ann.review_status == "rejected":
        raise ProjectError("却下した注釈は確定できません")
    flagged = needs_review(out, "annotation", annotation_id)
    if ann.review_status == "confirmed" and not flagged:
        return out
    ann.review_status = "confirmed"
    ann.revision += 1
    return clear_review_flags(out, "annotation", annotation_id)


def reject_annotation(project: Project, annotation_id: str) -> Project:
    out = project.model_copy(deep=True)
    ann = _find(out.annotations, "annotation_id", annotation_id, "annotation")
    if ann.review_status == "rejected":
        return out
    ann.review_status = "rejected"
    ann.revision += 1
    return clear_review_flags(out, "annotation", annotation_id)
