from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

import numpy as np
import tifffile
from openpyxl import load_workbook

from wb import config
from wb import project as P


@dataclass
class Source:
    source_id: str
    archive_member: str
    sha256: str
    size_bytes: int
    kind: str
    metadata: dict = field(default_factory=dict)
    exposure_seconds: float | None = None
    modality: str | None = None
    group_id: str | None = None
    scn_source_id: str | None = None
    exposure_matches_scn: bool | None = None
    analysis_source_id: str | None = None
    capture_eligible: bool = True
    relative_path: str | None = None


@dataclass
class Ledger:
    archive_name: str
    archive_sha256: str
    sources: list[Source]
    ignored: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_project_records(self) -> dict:
        sources, frames, captures = [], [], []
        by_id = {s.source_id: s for s in self.sources}
        images = [s for s in self.sources if s.capture_eligible and
                  (s.kind == "tiff" or s.metadata.get("display_mapping"))]
        for s in self.sources:
            base = {
                "source_asset_id": s.source_id,
                "archive_member": s.archive_member,
                "relative_path": s.relative_path or s.archive_member,
                "sha256": s.sha256,
                "size_bytes": s.size_bytes,
            }
            m = s.metadata
            if s.kind == "tiff":
                base.update(
                    representation_kind="presentation_export_candidate",
                    image={k: m[k] for k in ("width", "height", "dtype", "channels", "page_count", "orientation")},
                    quant_eligibility=m["quant_eligibility"],
                    red_overlay_present=m["red_overlay_present"],
                    native_source_asset_id=s.scn_source_id,
                )
            elif s.kind == "raw16":
                base.update(
                    representation_kind="analysis_export_candidate",
                    image={
                        k: m[k]
                        for k in ("width", "height", "dtype", "channels", "page_count", "photometric", "orientation")
                    },
                    quant_eligibility="unverified",
                )
                if m.get("display_mapping"):
                    base.update(display_mapping=m["display_mapping"],
                                red_overlay_present=m["saturated_pixel_count"] > 0,
                                native_source_asset_id=s.scn_source_id)
                frames.append(
                    dict(
                        frame_id="FRAME_" + s.source_id,
                        source_asset_id=s.source_id,
                        page_index=0,
                        channel_selection=m["channels"],
                        width=m["width"],
                        height=m["height"],
                        coordinate_space="analysis_full_px",
                    )
                )
            else:
                base.update(
                    representation_kind="native_container",
                    declared_image_size=m["size_pix"],
                    pixel_decode_performed=False,
                    quant_eligibility="unverified",
                )
            sources.append(base)
        for s in images:
            m = s.metadata
            if s.kind == "tiff":
                frames.append(
                    dict(
                        frame_id="FRAME_" + s.source_id,
                        source_asset_id=s.source_id,
                        page_index=0,
                        channel_selection="RGB_display" if m["channels"] == "RGB" else m["channels"],
                        width=m["width"],
                        height=m["height"],
                        coordinate_space="source_full_px",
                    )
                )
            scn = by_id.get(s.scn_source_id)
            matched = s.exposure_matches_scn is True
            captures.append(
                dict(
                    capture_id="CAP_" + s.source_id,
                    presentation_source_asset_id=s.source_id,
                    native_source_asset_id=s.scn_source_id,
                    analysis_source_asset_id=s.analysis_source_id,
                    link_status="filename_and_exposure_matched_geometry_unverified" if matched else "candidate",
                    exposure_s=scn.metadata["exposure_seconds"] if matched else s.exposure_seconds,
                    exposure_source="scn.scan_attributes.exposure_time.value" if matched else "filename",
                    modality=s.modality,
                    acquisition_mode="sam_candidate"
                    if scn and scn.metadata["exposure_mode"] == "2" and scn.metadata["sam_image_count"] is not None
                    else "not_sam_indicated",
                    analysis_to_presentation_transform_id=None,
                )
            )
            if s.kind == "raw16":
                captures[-1]["link_evidence"] = "Display derived directly from this raw16 frame; coordinates unchanged."
            elif s.analysis_source_id:
                analysis = by_id[s.analysis_source_id].metadata
                captures[-1]["link_evidence"] = (
                    f"Filename pair; dimension ratio candidate: {m['width']}/{analysis['width']} (width), "
                    f"{m['height']}/{analysis['height']} (height); geometry unverified."
                )
        markers = [s.source_id for s in images if s.modality == "colorimetric"]
        groups = sorted({s.group_id for s in images if s.group_id and s.modality == "chemiluminescence"})
        image_sets = [
            dict(
                image_set_id=g,
                grouping_source="archive_folder",
                grouping_status="candidate",
                reference_frame_id=None,
                geometry_alignment_status="unreviewed",
                capture_ids=["CAP_" + s.source_id for s in images if s.group_id == g],
                marker_source_candidate_ids=markers.copy(),
                marker_registration_status="unreviewed",
            )
            for g in groups
        ]
        return dict(
            sources=sources,
            frames=frames,
            captures=captures,
            image_sets=image_sets,
            input_archive={"name": self.archive_name, "sha256": self.archive_sha256},
        )


@dataclass
class MetadataTable:
    panels: list[dict]
    image_sets: list[dict]


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    # Treat backslashes as separators too, including on POSIX systems.
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or ".." in normalized.split("/"):
        raise ValueError(f"ZIP内に安全でないパスがあります: {name!r}")
    p = PurePosixPath(normalized)
    if not p.parts or any(re.match(r"^[A-Za-z]:", part) for part in p.parts):
        raise ValueError(f"ZIP内に安全でないパスがあります: {name!r}")
    return p


def _tiff_metadata(data: bytes, *, count_red: bool = True) -> dict:
    with tifffile.TiffFile(io.BytesIO(data)) as tif:
        page = tif.pages[0]
        samples = int(page.samplesperpixel)
        photometric = int(page.photometric) if page.photometric is not None else None
        rgb = photometric == 2 and samples >= 3
        count = 0
        saturated = 0
        if samples == 1 and page.dtype == np.dtype("uint16"):
            saturated = int(np.count_nonzero(page.asarray() == 65535))
        if rgb and count_red:
            a = page.asarray()
            if page.planarconfig == 2:
                a = np.moveaxis(a, 0, -1)
            count = int(np.count_nonzero((a[..., 0] > 240) & (a[..., 1] < 20) & (a[..., 2] < 20)))
        orientation = page.tags.get("Orientation")
        return dict(
            width=int(page.imagewidth),
            height=int(page.imagelength),
            dtype=str(page.dtype),
            samples_per_pixel=samples,
            page_count=len(tif.pages),
            photometric=photometric,
            orientation=int(orientation.value) if orientation else None,
            channels="RGB" if rgb else ("grayscale" if samples == 1 else str(samples)),
            red_overlay_pixel_count=count,
            red_overlay_present=count > 0,
            quant_eligibility="presentation_only" if rgb and page.dtype == np.dtype("uint8") else "unverified",
            saturated_pixel_count=saturated,
        )


def _scn_metadata(data: bytes) -> dict:
    roots = []
    for match in re.finditer(rb"<!DOCTYPE XML>\s*(<root(?:\s[^>]*)?>.*?</root\s*>)", data, re.DOTALL):
        block = match.group(1)
        if b"<!" in block or b"<?" in block:
            raise ValueError("SCNのXMLに許可されない宣言があります")
        roots.append(ET.fromstring(block))
    if not roots:
        raise ValueError("SCNに完全なXMLブロックがありません")

    def element(path):
        return next((e for root in roots if (e := root.find(path)) is not None), None)

    def attr(path, name):
        e = element(path)
        return e.get(name) if e is not None else None

    def value(path):
        e = element(path)
        return e.text.strip() if e is not None and e.text else None

    width, height = attr(".//size_pix", "width"), attr(".//size_pix", "height")
    exposure = attr(".//scan_attributes/exposure_time", "value")
    transform = element(".//scan_0/transform")
    return dict(
        xml_block_count=len(roots),
        size_pix=[int(width), int(height)] if width and height else None,
        application=attr(".//scan_attributes/application", "value"),
        exposure_seconds=float(exposure) if exposure is not None else None,
        exposure_mode=value(".//protocol_settings/exposure_mode"),
        sam_image_count=value(".//protocol_settings/sam_image_count"),
        first_image_time=value(".//protocol_settings/first_image_time"),
        last_image_time=value(".//protocol_settings/last_image_time"),
        data_ceiling=attr(".//scanner", "data_ceiling"),
        zero_is=attr(".//image", "zero_is"),
        display_transform=dict(transform.attrib) if transform is not None else {},
        pixel_decode_performed=False,
    )


def _kind(name: str, rules) -> str:
    lower = name.lower()
    return "scn" if lower.endswith(".scn") else "raw16" if rules.is_raw16(name) else "tiff"


def scan_archive(zip_path: str | Path) -> Ledger:
    path = Path(zip_path)
    archive_hash = _hash_file(path)
    rules = config.naming()
    ignored, entries = [], {}
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            p = _safe_member(info.filename)
            if (
                info.is_dir()
                or "__MACOSX" in p.parts
                or any(part.startswith("._") or part == ".DS_Store" for part in p.parts)
            ):
                continue
            if p.suffix.lower() not in (".tif", ".tiff", ".scn"):
                ignored.append(info.filename)
                continue
            canonical = str(p)
            if canonical in entries:
                raise ValueError(f"ZIP内のパスが重複しています: {canonical}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"ZIP内のシンボリックリンクは使えません: {canonical}")
            data = z.read(info)
            kind = _kind(p.name, rules)
            entries[canonical] = Source(
                "",
                canonical,
                hashlib.sha256(data).hexdigest(),
                len(data),
                kind,
                _scn_metadata(data) if kind == "scn" else _tiff_metadata(data, count_red=kind == "tiff"),
            )
    prefix = _archive_prefix(list(entries))
    for source in entries.values():
        source.relative_path = source.archive_member.removeprefix(prefix)
    return _link_entries(path.name, archive_hash, entries, ignored, rules)


def _archive_prefix(names):
    parts = [PurePosixPath(name).parts for name in names]
    if parts and all(len(p) > 1 and p[0] == parts[0][0] for p in parts):
        return parts[0][0] + "/"
    return ""


def _link_entries(name, digest, entries, ignored, rules):
    """Pair display TIFFs with their raw16 and .scn partners using the configured naming rules."""
    parsed = lambda member: rules.parse(PurePosixPath(member).name)
    images = sorted(
        (s for s in entries.values() if s.kind == "tiff"),
        key=lambda s: (PurePosixPath(s.archive_member).name, s.archive_member),
    )
    folder_names = sorted(
        {
            str(PurePosixPath(s.archive_member).parent)
            for s in entries.values()
            if (m := parsed(s.archive_member)) and m["modality"] == "chemiluminescence"
        }
    )
    groups = {folder: f"S{i:02d}" for i, folder in enumerate(folder_names, 1)}
    ordered, linked, linked_raw, warnings = [], set(), set(), []
    has_raw16 = any(s.kind == "raw16" for s in entries.values())
    for i, s in enumerate(images, 1):
        s.source_id = f"IMG{i:03d}"
        p = PurePosixPath(s.archive_member)
        m = parsed(s.archive_member)
        if m:
            s.exposure_seconds = m["exposure_s"]
            s.modality = m["modality"]
            if s.modality == "chemiluminescence":
                s.group_id = groups[str(p.parent)]
            else:
                s.group_id = "MARKER"
            scn_path = str(p.with_name(m["scn_name"])) if m["scn_name"] else None
            scn = entries.get(scn_path) if scn_path else None
            if scn and scn.kind == "scn":
                scn.source_id = s.source_id + "_SCN"
                s.scn_source_id = scn.source_id
                scn_exp = scn.metadata["exposure_seconds"]
                s.exposure_matches_scn = abs(scn_exp - s.exposure_seconds) < 0.0000005 if scn_exp is not None else None
                linked.add(scn_path)
        raw_path = str(p.with_name(rules.raw16_name(p.name)))
        raw = entries.get(raw_path)
        if raw and raw.kind == "raw16":
            raw.source_id = s.source_id + "_RAW16"
            s.analysis_source_id = raw.source_id
            linked_raw.add(raw_path)
        elif has_raw16:
            s.capture_eligible = False
            warnings.append(f"missing raw16 partner for display TIFF: {s.archive_member}")
        ordered.append(s)
        if s.scn_source_id:
            ordered.append(scn)
        if s.analysis_source_id:
            ordered.append(raw)
    for i, s in enumerate(
        sorted(
            (s for s in entries.values() if s.kind == "raw16" and s.archive_member not in linked_raw),
            key=lambda s: s.archive_member,
        ),
        1,
    ):
        s.source_id = f"RAW16_UNPAIRED{i:03d}"
        p = PurePosixPath(s.archive_member)
        m = parsed(s.archive_member)
        if m and s.metadata["dtype"] == "uint16" and s.metadata["channels"] == "grayscale" and s.metadata["photometric"] in (0, 1):
            s.exposure_seconds = m["exposure_s"]
            s.modality = m["modality"]
            s.group_id = groups[str(p.parent)] if s.modality == "chemiluminescence" else "MARKER"
            s.analysis_source_id = s.source_id
            # One fixed linear mapping for every exposure; no percentile clipping.
            s.metadata["display_mapping"] = {
                "method": "linear_uint16_full_range", "lower": 0, "upper": 65535,
                "invert": s.metadata["photometric"] == 0, "saturation_value": 65535,
            }
            scn_path = str(p.with_name(m["scn_name"])) if m["scn_name"] else None
            scn = entries.get(scn_path) if scn_path else None
            if scn and scn.kind == "scn":
                scn.source_id = s.source_id + "_SCN"
                s.scn_source_id = scn.source_id
                scn_exp = scn.metadata["exposure_seconds"]
                s.exposure_matches_scn = abs(scn_exp - s.exposure_seconds) < .0000005 if scn_exp is not None else None
                linked.add(scn_path)
                ordered.append(scn)
        ordered.append(s)
        warnings.append(f"missing display TIFF partner for raw16: {s.archive_member}")
    for i, s in enumerate(
        sorted(
            (s for s in entries.values() if s.kind == "scn" and s.archive_member not in linked),
            key=lambda s: s.archive_member,
        ),
        1,
    ):
        s.source_id = f"SCN_UNPAIRED{i:03d}"
        ordered.append(s)
    return Ledger(name, digest, ordered, ignored, warnings)


def _archive_files(zip_path):
    files = {}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            path = _safe_member(info.filename)
            if "__MACOSX" in path.parts or any(p.startswith("._") or p == ".DS_Store" for p in path.parts):
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"ZIP内のシンボリックリンクは使えません: {path}")
            if info.is_dir():
                continue
            if str(path) in files:
                raise ValueError(f"ZIP内でパスが重複しています: {path}")
            files[str(path)] = info.filename
    return files


def archive_root(zip_path):
    path = Path(zip_path).absolute()
    names = _archive_files(path)
    prefix = _archive_prefix(list(names))
    return path.parent / (prefix.rstrip("/") if prefix else path.stem)


def materialize_archive(zip_path, destination=None):
    """Extract once, publishing the complete verified directory with a rename."""
    path = Path(zip_path)
    files = _archive_files(path)
    prefix = _archive_prefix(list(files))
    root = Path(destination) if destination is not None else archive_root(path)
    if root.is_symlink():
        raise ValueError(f"展開先がシンボリックリンクです: {root}")
    with zipfile.ZipFile(path) as archive:
        manifest = {}
        for name, member in files.items():
            relative = name.removeprefix(prefix)
            if relative == "wblot_output" or relative.startswith("wblot_output/") or relative.endswith(".wblot"):
                raise ValueError(f"ZIP内に予約された出力パスがあります: {name}")
            with archive.open(member) as inp:
                manifest[relative] = hashlib.file_digest(inp, "sha256").hexdigest()
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            for relative, digest in manifest.items():
                target = root / relative
                if any(
                    root.joinpath(*Path(relative).parts[:i]).is_symlink() for i in range(len(Path(relative).parts) + 1)
                ):
                    raise ValueError(f"展開先にシンボリックリンクがあります: {target}")
                if not target.is_file() or _hash_file(target) != digest:
                    raise ValueError(f"既存フォルダの内容がZIPと異なります: {target}")
            extras = [
                p
                for p in root.rglob("*")
                if p.is_file()
                and "wblot_output" not in p.relative_to(root).parts
                and p.suffix != ".wblot"
                and p.relative_to(root).as_posix() not in manifest
            ]
            if extras:
                raise ValueError(f"既存フォルダにZIPにないファイルがあります: {extras[0]}")
            return root
        root.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=root.parent, prefix=".wblot-extract-"))
        try:
            for name, member in files.items():
                relative = name.removeprefix(prefix)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as inp, target.open("wb") as out:
                    shutil.copyfileobj(inp, out)
                if _hash_file(target) != manifest[relative]:
                    raise ValueError(f"展開後のSHA-256が一致しません: {relative}")
                target.chmod(0o444)
            if root.exists():
                root.rmdir()
            staging.rename(root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return root


def extract_sources(zip_path, project_dir, ledger):
    """Extract ledger sources directly into an image folder, without a sources copy."""
    if _hash_file(Path(zip_path)) != ledger.archive_sha256:
        raise ValueError("ZIPのSHA-256が台帳と一致しません")
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for source in ledger.sources:
            relative = _safe_member(source.relative_path or source.archive_member)
            target = root.joinpath(*relative.parts)
            if any(root.joinpath(*relative.parts[:i]).is_symlink() for i in range(len(relative.parts) + 1)):
                raise ValueError(f"展開先がシンボリックリンクです: {target}")
            if target.exists():
                if not target.is_file() or _hash_file(target) != source.sha256:
                    raise ValueError(f"既存画像が異なります: {target}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".ingest-")
            try:
                with os.fdopen(fd, "wb") as out, archive.open(source.archive_member) as inp:
                    shutil.copyfileobj(inp, out)
                if _hash_file(Path(temporary)) != source.sha256:
                    raise ValueError(f"展開後のSHA-256が一致しません: {relative}")
                if os.name == "nt":
                    # A read-only temp file could not be cleaned up on Windows.
                    os.replace(temporary, target)
                else:
                    os.chmod(temporary, 0o444)
                    os.link(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)


def scan_folder(folder):
    """Read the same ledger in place; never copy or modify a source."""
    root = Path(folder)
    rules = config.naming()
    entries, ignored = {}, []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "wblot_output" in relative.parts or "__MACOSX" in relative.parts:
            continue
        if any(p.startswith("._") or p == ".DS_Store" for p in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"画像フォルダのシンボリックリンクは使えません: {relative}")
        if not path.is_file() or path.suffix == ".wblot":
            continue
        P.require_local([path])
        member = root.name + "/" + relative.as_posix()
        if path.suffix.lower() not in (".tif", ".tiff", ".scn"):
            ignored.append(member)
            continue
        kind = _kind(path.name, rules)
        data = path.read_bytes()
        metadata = _scn_metadata(data) if kind == "scn" else _tiff_metadata(data, count_red=kind == "tiff")
        entries[member] = Source(
            "", member, hashlib.sha256(data).hexdigest(), len(data), kind, metadata, relative_path=relative.as_posix()
        )
    manifest = [(key, source.sha256) for key, source in sorted(entries.items())]
    digest = hashlib.sha256(repr(manifest).encode()).hexdigest()
    return _link_entries(root.name, digest, entries, ignored, rules)


def read_metadata_xlsx(path: str | Path, column_map: dict[str, str] | None = None) -> MetadataTable:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Panels" not in workbook:
            raise ValueError("Panelsシートがありません")
        mapping = column_map or {}

        def rows(sheet):
            iterator = workbook[sheet].iter_rows(values_only=True)
            headers = next(iterator, ())
            names = [str(x).strip() if x is not None else None for x in headers]
            names = [next((key for key, value in mapping.items() if value == name), name) for name in names]
            nonblank = [n for n in names if n]
            if len(nonblank) != len(set(nonblank)):
                raise ValueError("列名が重複しています")
            result = []
            for values in iterator:
                record = {
                    name: (None if value is None or isinstance(value, str) and not value.strip() else value)
                    for name, value in zip(names, values)
                    if name
                }
                if not any(v is not None for v in record.values()):
                    continue
                if sheet == "Panels":
                    for name in ("membrane_label", "image_set_id", "label", "lane_count", "sample_group_id"):
                        record.setdefault(name, None)
                    count = record["lane_count"]
                    if count is not None:
                        if (
                            isinstance(count, bool)
                            or not isinstance(count, (int, float))
                            or int(count) != count
                            or count < 1
                        ):
                            raise ValueError(f"lane_countが不正です: {count!r}")
                        record["lane_count"] = int(count)
                    record["display_order"] = len(result) + 1
                result.append(record)
            return result

        return MetadataTable(rows("Panels"), rows("ImageSets") if "ImageSets" in workbook else [])
    finally:
        workbook.close()
