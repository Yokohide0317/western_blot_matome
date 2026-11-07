import dataclasses
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from wb.ingest import extract_sources, read_metadata_xlsx, scan_archive


@pytest.fixture(scope="module")
def ledger(input_zip):
    return scan_archive(input_zip)


def test_real_audit(ledger, audit):
    images = [s for s in ledger.sources if s.kind == "tiff"]
    scns = [s for s in ledger.sources if s.kind == "scn"]
    assert len(images) == len(scns) == 50
    assert sum(s.modality == "chemiluminescence" for s in images) == 49
    assert sum(s.modality == "colorimetric" for s in images) == 1
    assert not any(Path(x).suffix.lower() in (".xls", ".xlsx") for x in ledger.ignored)
    native = {s.source_id: s for s in scns}
    for s, expected in zip(images, audit["tiff_images"], strict=True):
        m = s.metadata
        assert (s.source_id, s.sha256, s.exposure_seconds, s.group_id) == (
            expected["source_id"],
            expected["sha256"],
            expected["exposure_s_from_filename"],
            expected["acquisition_group_candidate"],
        )
        assert (m["red_overlay_pixel_count"], m["red_overlay_present"]) == (
            expected["red_overlay_pixel_count"],
            expected["red_overlay_present"],
        )
        assert (m["width"], m["height"], m["dtype"], m["channels"], m["samples_per_pixel"], m["page_count"]) == (
            1830,
            1464,
            "uint8",
            "RGB",
            3,
            1,
        )
        assert m["quant_eligibility"] == "presentation_only"
        n = native[s.scn_source_id]
        assert n.sha256 == expected["native_scn_sha256"]
        assert n.metadata["size_pix"] == [expected["native_declared_width"], expected["native_declared_height"]]
        assert s.exposure_matches_scn
    assert sum(s.metadata["red_overlay_present"] for s in images) == 21
    assert sum(s.metadata["size_pix"] == [1375, 1100] for s in scns) == 49
    assert sum(s.metadata["size_pix"] == [2750, 2200] for s in scns) == 1
    assert ledger.archive_sha256 == audit["summary"]["input_archive_sha256"]


def test_seed(ledger, seed):
    records = ledger.to_project_records()
    assert records["input_archive"] == seed["input_archive"]
    for key in ("sources", "frames", "captures", "image_sets"):
        assert len(records[key]) == len(seed[key])
        for actual, expected in zip(records[key], seed[key], strict=True):
            if key == "sources" and "image" in expected:
                assert actual["image"]["orientation"] == 1
                assert expected["image"]["orientation"] is None
                actual = {**actual, "image": {**actual["image"], "orientation": None}}
            assert {k: actual[k] for k in expected} == expected
    assert all(s["relative_path"] == s["archive_member"].split("/", 1)[1] for s in records["sources"])


@pytest.mark.parametrize("name", ["../evil.tif", "/abs.tif", "C:/evil.tif", "a/../../evil.tif", "a\\..\\evil.tif"])
def test_bad_paths(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, b"bad")
    with pytest.raises(ValueError):
        scan_archive(archive)


def test_skipped_and_ignored(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("__MACOSX/a.tif", b"bad")
        z.writestr("a/._file.tif", b"bad")
        z.writestr("a/.DS_Store", b"bad")
        z.writestr("a/unknown.py", b"not executed")
    ledger = scan_archive(archive)
    assert not ledger.sources
    assert ledger.ignored == ["a/unknown.py"]


def test_extract(input_zip, ledger, tmp_path):
    subset = dataclasses.replace(ledger, sources=ledger.sources[:2])
    extract_sources(input_zip, tmp_path, subset)
    paths = [tmp_path / s.relative_path for s in subset.sources]
    assert all(p.stat().st_mode & 0o777 == 0o444 for p in paths)
    times = [p.stat().st_mtime_ns for p in paths]
    extract_sources(input_zip, tmp_path, subset)
    assert times == [p.stat().st_mtime_ns for p in paths]
    paths[0].chmod(0o644)
    paths[0].write_bytes(b"changed")
    with pytest.raises(ValueError, match="異なります"):
        extract_sources(input_zip, tmp_path, subset)
    assert paths[0].read_bytes() == b"changed"


def test_excel(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Panels"
    sheet.append(["Membrane", "label", "lane_count", "sample_group_id", "note"])
    sheet.append(["I", "TargetA", None, None, "x"])
    sheet.append(["J", "TargetB", 4, "g", " "])
    images = book.create_sheet("ImageSets")
    images.append(["image_set_id", "marker_file"])
    images.append(["S01", None])
    path = tmp_path / "metadata.xlsx"
    book.save(path)
    table = read_metadata_xlsx(path, {"membrane_label": "Membrane"})
    assert table.panels[0] == {
        "membrane_label": "I",
        "label": "TargetA",
        "lane_count": None,
        "sample_group_id": None,
        "note": "x",
        "image_set_id": None,
        "display_order": 1,
    }
    assert table.panels[1]["lane_count"] == 4
    assert table.panels[1]["display_order"] == 2
    assert table.panels[1]["note"] is None
    assert table.image_sets == [{"image_set_id": "S01", "marker_file": None}]
    sheet["C3"] = 2.5
    book.save(path)
    with pytest.raises(ValueError, match="lane_count"):
        read_metadata_xlsx(path)


@pytest.fixture(scope="module")
def second_ledger(input_zip_second):
    return scan_archive(input_zip_second)


def test_second_archive_records(second_ledger):
    records = second_ledger.to_project_records()
    sources = {s["source_asset_id"]: s for s in records["sources"]}
    captures = records["captures"]
    assert len(captures) == 31
    assert sum(c["modality"] == "chemiluminescence" for c in captures) == 30
    assert sum(c["modality"] == "colorimetric" for c in captures) == 1
    assert [(s["image_set_id"], len(s["capture_ids"])) for s in records["image_sets"]] == [("S01", 10), ("S02", 20)]
    assert not second_ledger.warnings
    for capture in captures:
        raw = sources[capture["analysis_source_asset_id"]]
        assert raw["source_asset_id"] == capture["presentation_source_asset_id"] + "_RAW16"
        assert raw["representation_kind"] == "analysis_export_candidate"
        assert raw["quant_eligibility"] == "unverified"
        assert raw["image"]["dtype"] == "uint16"
        expected = (2750, 2200) if capture["modality"] == "colorimetric" else (1375, 1100)
        assert (raw["image"]["width"], raw["image"]["height"]) == expected
        assert capture["analysis_to_presentation_transform_id"] is None
        assert "dimension ratio candidate" in capture["link_evidence"]
    assert all("_RAW16" not in c for group in records["image_sets"] for c in group["capture_ids"])
    display_frames = [f for f in records["frames"] if f["coordinate_space"] == "source_full_px"]
    assert len(display_frames) == 31
    assert all("_RAW16" not in f["source_asset_id"] for f in display_frames)
    assert sum(f["coordinate_space"] == "analysis_full_px" for f in records["frames"]) == 31


def test_second_archive_import(input_zip_second, tmp_path):
    from wb.api import import_project
    from wb.project import load_project, locate

    project = import_project(input_zip_second, tmp_path)
    assert len(project.captures) == len(load_project(locate(tmp_path)).captures) == 31
    raw = [s for s in project.sources if s.representation_kind == "analysis_export_candidate"]
    assert len(raw) == 31
    assert all((tmp_path / s.relative_path).stat().st_mode & 0o777 == 0o444 for s in raw)


def test_orphan_raw16(tmp_path):
    import io
    import numpy as np
    import tifffile

    data = io.BytesIO()
    tifffile.imwrite(data, np.zeros((4, 5), dtype=np.uint16))
    archive = tmp_path / "orphan.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("folder/orphan.raw16.tif", data.getvalue())
    ledger = scan_archive(archive)
    records = ledger.to_project_records()
    assert len(ledger.warnings) == 1 and "orphan.raw16.tif" in ledger.warnings[0]
    assert len(records["sources"]) == 1
    assert records["sources"][0]["representation_kind"] == "analysis_export_candidate"
    assert not records["captures"]


def test_missing_raw16_and_unmatched_exposure(tmp_path):
    import io
    import numpy as np
    import tifffile

    data = io.BytesIO()
    tifffile.imwrite(data, np.zeros((4, 5), dtype=np.uint16))
    archive = tmp_path / "missing.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a/1.0s(Chemiluminescence).tif", data.getvalue())
        z.writestr("a/2.0s(Chemiluminescence).tif", data.getvalue())
        z.writestr("a/2.0s(Chemiluminescence).raw16.tif", data.getvalue())
    ledger = scan_archive(archive)
    records = ledger.to_project_records()
    assert len(ledger.warnings) == 1 and "1.0s(Chemiluminescence).tif" in ledger.warnings[0]
    assert len(records["sources"]) == 3
    assert len(records["captures"]) == 1
    assert records["captures"][0]["link_status"] == "candidate"
    from wb.project import new_project

    assert len(new_project("test", records).captures) == 1


def test_folder_scan_and_archive_reuse(input_zip, tmp_path):
    from wb import api, project as P
    from wb.ingest import materialize_archive, scan_folder

    archive = tmp_path / input_zip.name
    archive.symlink_to(input_zip)
    root = materialize_archive(archive)
    sources_before = {p.relative_to(root): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    folder_ledger = scan_folder(root)
    archive_ledger = scan_archive(archive)
    assert [(s.source_id, s.sha256, s.relative_path) for s in folder_ledger.sources] == [
        (s.source_id, s.sha256, s.relative_path) for s in archive_ledger.sources
    ]
    project = api.import_project(root)
    assert P.locate(archive) == root / (root.name + ".wblot")
    assert api.import_project(root) == project
    assert materialize_archive(archive) == root
    assert all((root / relative).stat().st_mtime_ns == stamp for relative, stamp in sources_before.items())
    first = root / project.sources[0].relative_path
    first.chmod(0o644)
    first.write_bytes(b"changed")
    with pytest.raises(ValueError, match="異なります"):
        materialize_archive(archive)


@pytest.mark.parametrize("name", ["folder/linked.txt", "folder/linked.tif"])
def test_archive_symlinks_rejected_before_extraction(tmp_path, name):
    from wb.ingest import materialize_archive

    archive = tmp_path / "bad.zip"
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(info, "elsewhere")
    with pytest.raises(ValueError, match="シンボリックリンク"):
        materialize_archive(archive)
    assert not (tmp_path / "folder").exists()
