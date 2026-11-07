import copy
import hashlib
import json
import os

import pytest

from wb import project as P
from wb.model import DisplayGeometry, Project


# ---------------------------------------------------------------- helpers


def write_seed(tmp_path, seed):
    path = tmp_path / "project.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    return path


def seed_records(seed):
    return {
        k: copy.deepcopy(seed[k])
        for k in ("input_archive", "sources", "frames", "captures", "image_sets")
    }


def contains(sub, whole):
    """True if everything in `sub` is present, unchanged, in `whole`."""
    if isinstance(sub, dict):
        return all(k in whole and contains(v, whole[k]) for k, v in sub.items())
    if isinstance(sub, list):
        return len(sub) == len(whole) and all(contains(a, b) for a, b in zip(sub, whole))
    return sub == whole


def without_history(project):
    return project.model_dump(exclude={"history"})


def point(x=1.0, y=2.0):
    return {"type": "point", "coordinates": [[x, y]]}


def make_annotation(panel_id, frame_id, **over):
    base = {
        "panel_id": panel_id,
        "frame_id": frame_id,
        "purpose": "band",
        "geometry": point(),
        "created_by": "human",
    }
    return {**base, **over}


def geometry(frame_id, width=600):
    return DisplayGeometry(
        reference_frame_id=frame_id,
        crop_rect={"x0": 10, "y0": 20, "x1": 110, "y1": 60},
        target_width_px=width,
    )


@pytest.fixture
def seeded(seed):
    return P.new_project("test", seed_records(seed), seed=seed)


@pytest.fixture
def data_dir(tmp_path):
    """Two real files and a project whose sources point at them."""
    d = tmp_path / "proj"
    d.mkdir()
    files = {"A": b"alpha-bytes", "B": b"bravo-bytes"}
    records = {"sources": [], "frames": [], "captures": [], "image_sets": []}
    for name, content in files.items():
        (d / f"{name}.tif").write_bytes(content)
        records["sources"].append(
            {
                "source_asset_id": f"SRC_{name}",
                "representation_kind": "presentation_export_candidate",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "relative_path": f"{name}.tif",
                "image": {"width": 4, "height": 3, "dtype": "uint8", "channels": "RGB"},
                "quant_eligibility": "presentation_only",
            }
        )
        records["frames"].append({"frame_id": f"FRAME_{name}", "source_asset_id": f"SRC_{name}"})
        records["captures"].append(
            {"capture_id": f"CAP_{name}", "presentation_source_asset_id": f"SRC_{name}"}
        )
    records["image_sets"].append(
        {"image_set_id": "SET", "capture_ids": ["CAP_A", "CAP_B"]}
    )
    p = P.new_project("small", records)
    p = P.add_region(p, "K", "TargetC")
    p = P.add_region(p, "L", "TargetC")
    return d, p


# ------------------------------------------------------- seed / migration


def test_seed_loads_via_migration_and_keeps_user_mapping(tmp_path, seed):
    p = P.load_project(write_seed(tmp_path, seed))
    assert p.schema_version == "1.0"
    labels = {r.user_label: r.target_label for r in p.membrane_regions}
    assert set(labels) == {"I", "J", "K", "L"} and all(labels.values())
    assert labels["K"] == labels["L"] and len({labels["I"], labels["J"], labels["K"]}) == 3
    assert next(r for r in p.membrane_regions if r.user_label == "K").region_id != next(
        r for r in p.membrane_regions if r.user_label == "L"
    ).region_id
    actin = p.unassigned_target_rules[0]
    assert actin.target_label == "Actin" and actin.control_for_panel_ids == []
    for r in p.membrane_regions:
        assert r.geometry is None and r.lane_count is None and r.image_set_id is None
    for panel in p.panels:
        assert panel.display_geometry is None and panel.selected_exposure_id is None
    assert p.is_executable_project is False
    assert p.migrated_from["schema_version"] == "1.0-draft"
    assert p.migrated_from["original"] == seed
    assert p.history[-1].operation == "migrate_1_0_draft"


def test_seed_round_trips_through_save_and_load_without_loss(tmp_path, seed):
    p = P.load_project(write_seed(tmp_path, seed))
    out = tmp_path / "saved.json"
    saved = P.save_project(p, out, expected_revision=p.project_revision)
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    expected = {k: v for k, v in seed.items() if k not in ("schema_version", "project_revision")}
    assert contains(expected, on_disk)
    assert on_disk["schema_version"] == "1.0"
    assert P.load_project(out) == saved


def test_migration_does_not_touch_the_input_dict(seed):
    before = copy.deepcopy(seed)
    P.migrate_1_0_draft(seed)
    assert seed == before


# ------------------------------------------------- versions and revisions


@pytest.mark.parametrize("version", ["2.0", "1.1", "0.9", None])
def test_unsupported_schema_version_raises(tmp_path, seed, version):
    data = copy.deepcopy(seed)
    data["schema_version"] = version
    with pytest.raises(P.UnsupportedSchemaVersion):
        P.load_project(write_seed(tmp_path, data))


def test_stale_expected_revision_raises_and_keeps_file(tmp_path, seeded):
    path = tmp_path / "project.json"
    first = P.save_project(seeded, path, expected_revision=1)
    assert first.project_revision == 2
    before = path.read_bytes()
    with pytest.raises(P.RevisionConflict):
        P.save_project(seeded, path, expected_revision=1)
    assert path.read_bytes() == before
    second = P.save_project(first, path, expected_revision=2)
    assert second.project_revision == 3
    assert [h.project_revision for h in second.history][-2:] == [2, 3]


def test_save_does_not_mutate_its_input(tmp_path, seeded):
    P.save_project(seeded, tmp_path / "p.json", expected_revision=1)
    assert seeded.project_revision == 1


@pytest.mark.parametrize("target", ["os.replace", "os.fsync"])
def test_failed_write_leaves_previous_file_intact(tmp_path, seeded, monkeypatch, target):
    path = tmp_path / "project.json"
    P.save_project(seeded, path, expected_revision=1)
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(target, boom)
    changed = P.set_selected_exposure(seeded, "PANEL_K", "CAP_IMG001")
    with pytest.raises(OSError):
        P.save_project(changed, path, expected_revision=2)
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert P.load_project(path).project_revision == 2
    assert [f.name for f in tmp_path.iterdir()] == ["project.json"]


# ------------------------------------------------------ K / L independence


def test_changes_to_panel_k_leave_panel_l_unchanged(seeded):
    l_before = next(p for p in seeded.panels if p.panel_id == "PANEL_L").model_copy(deep=True)
    p = P.set_selected_exposure(seeded, "PANEL_K", "CAP_IMG005")
    p = P.set_display_geometry(p, "PANEL_K", geometry("FRAME_IMG010"))
    p = P.add_annotation(p, make_annotation("PANEL_K", "FRAME_IMG010"))
    p = P.add_annotation(p, make_annotation("PANEL_K", "FRAME_IMG010", purpose="background_roi"))

    panels = {x.panel_id: x for x in p.panels}
    assert panels["PANEL_L"] == l_before
    assert panels["PANEL_K"].selected_exposure_id == "CAP_IMG005"
    assert panels["PANEL_K"].display_geometry.geometry_revision == 1
    assert len(panels["PANEL_K"].band_annotation_ids) == 1
    assert len(panels["PANEL_K"].background_roi_ids) == 1
    assert {a.panel_id for a in p.annotations} == {"PANEL_K"}


def test_geometry_revision_increments_per_panel(seeded):
    p = P.set_display_geometry(seeded, "PANEL_K", geometry("FRAME_IMG010"))
    p = P.set_display_geometry(p, "PANEL_K", geometry("FRAME_IMG010", width=800))
    p = P.set_display_geometry(p, "PANEL_L", geometry("FRAME_IMG010"))
    rev = {x.panel_id: x.display_geometry and x.display_geometry.geometry_revision for x in p.panels}
    assert rev["PANEL_K"] == 2 and rev["PANEL_L"] == 1 and rev["PANEL_I"] is None


def test_selected_exposure_must_belong_to_the_regions_image_set(seeded):
    p = seeded.model_copy(deep=True)
    next(r for r in p.membrane_regions if r.region_id == "REGION_K").image_set_id = "S01"
    p = P.set_selected_exposure(p, "PANEL_K", "CAP_IMG002")
    with pytest.raises(P.ProjectError):
        P.set_selected_exposure(p, "PANEL_K", "CAP_IMG005")
    with pytest.raises(P.UnknownId):
        P.set_selected_exposure(p, "PANEL_K", "CAP_NOPE")
    with pytest.raises(P.UnknownId):
        P.set_selected_exposure(p, "PANEL_NOPE", "CAP_IMG002")
    assert P.set_selected_exposure(p, "PANEL_K", None).panels[2].selected_exposure_id is None


def test_helpers_do_not_mutate_their_input(seeded):
    before = seeded.model_dump()
    P.set_selected_exposure(seeded, "PANEL_K", "CAP_IMG001")
    P.add_annotation(seeded, make_annotation("PANEL_K", "FRAME_IMG010"))
    assert seeded.model_dump() == before


# ------------------------------------------------------------- no-signal lane


def test_no_signal_lane_keeps_lane_count_and_order_after_save_load(tmp_path, seeded):
    from wb.model import SampleGroup

    names = ["ctrl", "dox", "dox+MG", "empty", "KO", "KO+dox"]
    group = SampleGroup(
        sample_group_id="SG_1",
        lane_count=6,
        samples=[
            {"name": n, "role": "negative_control" if n == "ctrl" else "sample"} for n in names
        ],
    )
    p = seeded.model_copy(deep=True)
    p.sample_groups.append(group)
    p.membrane_regions[2].lane_count = 6
    p.panels[2].sample_group_id = "SG_1"
    # lane 4 has no band: it is recorded as not_detected, with no ROI invented for it
    p = P.add_annotation(
        p,
        make_annotation(
            "PANEL_K", "FRAME_IMG010", lane_id=4, band_status="not_detected", geometry=point(50, 60)
        ),
    )
    path = tmp_path / "p.json"
    P.save_project(p, path, expected_revision=1)
    q = P.load_project(path)
    assert q.sample_groups[0].lane_count == 6
    assert [s.name for s in q.sample_groups[0].samples] == names
    assert q.membrane_regions[2].lane_count == 6
    lane4 = next(a for a in q.annotations if a.lane_id == 4)
    assert lane4.band_status == "not_detected"
    assert lane4.quantification_status == "not_requested"


# ------------------------------------------------------------ annotations


def test_annotation_validation_and_defaults(seeded):
    p = P.add_annotation(seeded, make_annotation("PANEL_I", "FRAME_IMG010"))
    a = p.annotations[0]
    assert a.confidence is None and a.review_status == "draft"
    with pytest.raises(ValueError):
        P.add_annotation(
            seeded,
            make_annotation(
                "PANEL_I", "FRAME_IMG010", geometry={"type": "point", "coordinates": [[1, 2], [3, 4]]}
            ),
        )
    with pytest.raises(ValueError):
        P.add_annotation(seeded, make_annotation("PANEL_I", "FRAME_IMG010", created_by="robot"))
    with pytest.raises(P.UnknownId):
        P.add_annotation(seeded, make_annotation("PANEL_NOPE", "FRAME_IMG010"))
    with pytest.raises(P.UnknownId):
        P.add_annotation(seeded, make_annotation("PANEL_I", "FRAME_NOPE"))


def test_polygon_annotation_is_stored_as_given(seeded):
    poly = {"type": "polygon", "coordinates": [[0.5, 0.5], [9.5, 0.5], [9.5, 4.25]]}
    p = P.add_annotation(
        seeded, make_annotation("PANEL_I", "FRAME_IMG010", purpose="measurement_roi", geometry=poly)
    )
    assert p.annotations[0].geometry.coordinates == poly["coordinates"]


def test_not_detected_is_not_turned_into_zero_or_negative(seeded):
    p = P.add_annotation(
        seeded, make_annotation("PANEL_I", "FRAME_IMG010", band_status="not_detected")
    )
    p = P.confirm_annotation(p, p.annotations[0].annotation_id)
    dumped = p.annotations[0].model_dump()
    assert dumped["band_status"] == "not_detected"
    assert dumped["quantification_status"] == "not_requested"
    assert not {"value", "intensity", "negative"} & dumped.keys()


def test_confirm_and_reject_transitions(seeded):
    p = P.add_annotation(seeded, make_annotation("PANEL_I", "FRAME_IMG010"))
    aid = p.annotations[0].annotation_id
    p = P.confirm_annotation(p, aid)
    assert p.annotations[0].review_status == "confirmed" and p.annotations[0].revision == 2
    assert P.confirm_annotation(p, aid).annotations[0].revision == 2  # no-op
    p = P.reject_annotation(p, aid)
    assert p.annotations[0].review_status == "rejected" and p.annotations[0].revision == 3
    with pytest.raises(P.ProjectError):
        P.confirm_annotation(p, aid)
    with pytest.raises(P.UnknownId):
        P.reject_annotation(p, "ANN_NOPE")


def test_annotations_cannot_be_added_already_confirmed(seeded):
    with pytest.raises(P.ProjectError):
        P.add_annotation(seeded, make_annotation("PANEL_I", "FRAME_IMG010", review_status="confirmed"))


def test_adding_the_same_annotation_twice_does_not_duplicate(seeded):
    data = make_annotation("PANEL_I", "FRAME_IMG010")
    once = P.add_annotation(seeded, data)
    twice = P.add_annotation(once, data)
    assert twice.annotations == once.annotations
    assert len(twice.panels[0].band_annotation_ids) == 1
    other = P.add_annotation(twice, make_annotation("PANEL_I", "FRAME_IMG010", geometry=point(9, 9)))
    assert len(other.annotations) == 2


# ----------------------------------------------------------- source changes


def test_verify_sources_reports_missing_and_changed_files(data_dir):
    d, p = data_dir
    assert P.verify_sources(p, d) == []
    (d / "A.tif").write_bytes(b"ALPHA-bytes")  # same size, different content
    (d / "B.tif").unlink()
    issues = {i.source_asset_id: i for i in P.verify_sources(p, d)}
    assert issues["SRC_A"].kind == "hash_changed"
    assert issues["SRC_B"].kind == "missing"


def test_size_change_is_reported_without_hashing(data_dir):
    d, p = data_dir
    (d / "A.tif").write_bytes(b"much longer than before")
    (issue,) = P.verify_sources(p, d)
    assert issue.kind == "size_changed"


def test_sources_without_a_path_are_not_checked(seeded, tmp_path):
    assert P.verify_sources(seeded, tmp_path) == []


def test_changed_source_flags_dependent_geometry_not_others(data_dir):
    d, p = data_dir
    p = P.set_display_geometry(p, "PANEL_K", geometry("FRAME_A"))
    p = P.set_display_geometry(p, "PANEL_L", geometry("FRAME_B"))
    p = P.add_annotation(p, make_annotation("PANEL_K", "FRAME_A"))
    p = P.add_annotation(p, make_annotation("PANEL_L", "FRAME_B"))
    ann_k = next(a for a in p.annotations if a.panel_id == "PANEL_K").annotation_id
    ann_l = next(a for a in p.annotations if a.panel_id == "PANEL_L").annotation_id
    coords_before = [a.geometry for a in p.annotations]

    (d / "A.tif").write_bytes(b"ALPHA-bytes")
    flagged = P.flag_dependents(p, P.verify_sources(p, d))

    assert P.needs_review(flagged, "annotation", ann_k)
    assert P.needs_review(flagged, "panel", "PANEL_K")
    assert not P.needs_review(flagged, "annotation", ann_l)
    assert not P.needs_review(flagged, "panel", "PANEL_L")
    # coordinates are kept for the person to re-check, not deleted
    assert [a.geometry for a in flagged.annotations] == coords_before
    assert {f.reason for f in flagged.review_flags} == {"source_hash_changed"}
    assert P.flag_dependents(flagged, P.verify_sources(p, d)).review_flags == flagged.review_flags

    saved = P.save_project(flagged, d / "project.json", expected_revision=1)
    assert P.load_project(d / "project.json").review_flags == saved.review_flags


def test_a_missing_file_alone_flags_nothing(data_dir):
    d, p = data_dir
    p = P.add_annotation(p, make_annotation("PANEL_K", "FRAME_A"))
    (d / "A.tif").unlink()
    assert P.flag_dependents(p, P.verify_sources(p, d)).review_flags == []


def test_flags_clear_only_by_explicit_review(data_dir):
    d, p = data_dir
    p = P.set_display_geometry(p, "PANEL_K", geometry("FRAME_A"))
    p = P.add_annotation(p, make_annotation("PANEL_K", "FRAME_A"))
    aid = p.annotations[0].annotation_id
    (d / "A.tif").write_bytes(b"ALPHA-bytes")
    p = P.flag_dependents(p, P.verify_sources(p, d))
    p = P.confirm_annotation(p, aid)
    assert not P.needs_review(p, "annotation", aid)
    assert P.needs_review(p, "panel", "PANEL_K")
    p = P.set_display_geometry(p, "PANEL_K", geometry("FRAME_A", width=700))
    assert p.review_flags == []


def test_reimport_with_changed_hash_flags_instead_of_reusing(data_dir):
    d, p = data_dir
    p = P.add_annotation(p, make_annotation("PANEL_K", "FRAME_A"))
    new_src = copy.deepcopy(p.sources[0].model_dump())
    new_src["sha256"] = hashlib.sha256(b"different").hexdigest()
    q = P.import_records(p, {"sources": [new_src]})
    assert q.sources[0].sha256 == new_src["sha256"]
    assert P.needs_review(q, "annotation", p.annotations[0].annotation_id)


def test_relink_by_hash_updates_path_only_on_exact_match(data_dir):
    d, p = data_dir
    (d / "moved").mkdir()
    (d / "A.tif").rename(d / "moved" / "renamed.tif")
    (d / "B.tif").write_bytes(b"BRAVO-bytes")  # same size as before, wrong content
    q = P.relink_by_hash(p, d)
    paths = {s.source_asset_id: s.relative_path for s in q.sources}
    assert paths["SRC_A"] == "moved/renamed.tif"
    assert paths["SRC_B"] == "B.tif"  # unchanged: no file has its hash
    assert [i.source_asset_id for i in P.verify_sources(q, d)] == ["SRC_B"]


# ------------------------------------------------------------- idempotency


def test_reimporting_the_same_records_does_not_duplicate(seed):
    records = seed_records(seed)
    once = P.new_project("x", records, seed=seed)
    twice = P.import_records(once, records)
    assert without_history(twice) == without_history(once)
    assert len(twice.sources) == 100 and len(twice.frames) == 50 and len(twice.captures) == 50
    assert len(twice.panels) == 4 and len(twice.membrane_regions) == 4
    assert P.new_project("x", records, seed=seed).model_dump(exclude={"history"}) == once.model_dump(
        exclude={"history"}
    )


def test_reimport_keeps_human_review_of_existing_records(seeded, seed):
    p = seeded.model_copy(deep=True)
    p.image_sets[0].reference_frame_id = "FRAME_IMG001"
    p.image_sets[0].geometry_alignment_status = "confirmed"
    q = P.import_records(p, seed_records(seed))
    assert q.image_sets[0].reference_frame_id == "FRAME_IMG001"
    assert q.image_sets[0].geometry_alignment_status == "confirmed"


def test_new_project_from_seed_keeps_labels_and_nulls(seeded):
    targets = {r.region_id: r.target_label for r in seeded.membrane_regions}
    assert [p.panel_id for p in seeded.panels] == ["PANEL_I", "PANEL_J", "PANEL_K", "PANEL_L"]
    assert [p.label for p in seeded.panels] == [targets[p.region_id] for p in seeded.panels]
    assert seeded.panels[2].label == seeded.panels[3].label
    assert seeded.unassigned_target_rules[0].rule_id == "ACTIN_THIN_REMAINDER"
    assert len(seeded.unresolved_items) == 5
    assert all(r.geometry is None and r.lane_count is None for r in seeded.membrane_regions)
    assert seeded.project_revision == 1 and seeded.schema_version == "1.0"
    assert all(s.relative_path is None for s in seeded.sources)


def test_new_project_does_not_carry_coordinates_from_a_filled_seed(seed):
    filled = copy.deepcopy(seed)
    filled["membrane_regions"][2]["lane_count"] = 6
    filled["membrane_regions"][2]["geometry"] = {
        "frame_id": "FRAME_IMG001",
        "polygon": [[0, 0], [1, 0], [1, 1]],
    }
    p = P.new_project("x", seed_records(seed), seed=filled)
    assert p.membrane_regions[2].lane_count is None and p.membrane_regions[2].geometry is None


def test_add_region_is_idempotent_and_keys_on_user_label(data_dir):
    d, p = data_dir
    again = P.add_region(p, "K", "TargetC")
    assert again.model_dump() == p.model_dump()
    assert [r.region_id for r in p.membrane_regions] == ["REGION_K", "REGION_L"]
    assert [x.panel_id for x in p.panels] == ["PANEL_K", "PANEL_L"]


def test_stable_id_is_deterministic_and_sensitive_to_input():
    assert P.stable_id("X", 1, "a") == P.stable_id("X", 1, "a")
    assert P.stable_id("X", 1, "a") != P.stable_id("X", 1, "b")
    assert P.stable_id("X", 1, "a") != P.stable_id("Y", 1, "a")
