import copy

import pytest
from pydantic import ValidationError

from wb.model import (
    Annotation,
    CropRect,
    LaneLayout,
    Project,
    SampleGroup,
    Source,
    Transform,
)

SHA = "a" * 64


def ann(**over):
    base = {
        "annotation_id": "ANN_1",
        "frame_id": "FRAME_A",
        "panel_id": "PANEL_K",
        "purpose": "band",
        "geometry": {"type": "point", "coordinates": [[10.5, 20.5]]},
        "created_by": "human",
    }
    return Annotation.model_validate({**base, **over})


def test_point_has_exactly_one_coordinate():
    ann()
    with pytest.raises(ValidationError):
        ann(geometry={"type": "point", "coordinates": [[1, 2], [3, 4]]})
    with pytest.raises(ValidationError):
        ann(geometry={"type": "point", "coordinates": []})


def test_polygon_needs_three_points_and_xy_pairs():
    ann(geometry={"type": "polygon", "coordinates": [[0, 0], [4, 0], [4, 4]]})
    with pytest.raises(ValidationError):
        ann(geometry={"type": "polygon", "coordinates": [[0, 0], [4, 0]]})
    with pytest.raises(ValidationError):
        ann(geometry={"type": "polygon", "coordinates": [[0, 0, 1], [4, 0, 1], [4, 4, 1]]})


def test_status_enums_reject_unknown_values():
    for field, bad in [
        ("created_by", "ai"),
        ("purpose", "roi"),
        ("band_status", "negative"),
        ("review_status", "approved"),
        ("quantification_status", "done"),
    ]:
        with pytest.raises(ValidationError):
            ann(**{field: bad})


def test_annotation_defaults_invent_nothing():
    a = ann()
    assert a.confidence is None and a.method is None and a.lane_id is None
    assert a.band_status == "not_reviewed"
    assert a.quantification_status == "not_requested"


def test_unknown_annotation_field_is_rejected():
    with pytest.raises(ValidationError):
        ann(band_statuss="detected")


@pytest.mark.parametrize(
    "matrix",
    [
        [[1, 0, 0], [0, 1, 0]],
        [[1, 0, 0], [0, 1, 0], [0, 0, 2]],
        [[1, 0, 0], [0, 1, 0], [1, 0, 1]],
        [[1, 0], [0, 1], [0, 0]],
    ],
)
def test_transform_matrix_must_be_affine_3x3(matrix):
    with pytest.raises(ValidationError):
        Transform(
            transform_id="TR_1",
            kind="exposure_to_reference",
            from_frame_id="A",
            to_frame_id="B",
            matrix=matrix,
        )


def test_transform_accepts_identity_and_defaults_to_candidate():
    t = Transform(
        transform_id="TR_1",
        kind="exposure_to_reference",
        from_frame_id="A",
        to_frame_id="B",
        matrix=[[1, 0, 5.5], [0, 1, 0], [0, 0, 1]],
    )
    assert t.status == "candidate"


def test_lane_count_is_none_or_positive_never_defaulted():
    assert LaneLayout().lane_count is None
    assert SampleGroup(sample_group_id="G").lane_count is None
    with pytest.raises(ValidationError):
        LaneLayout(lane_count=0)
    with pytest.raises(ValidationError):
        SampleGroup(sample_group_id="G", lane_count=0)


def test_sample_group_samples_must_match_lane_count():
    with pytest.raises(ValidationError):
        SampleGroup(
            sample_group_id="G", lane_count=3, samples=[{"name": "a"}, {"name": "b"}]
        )


def test_lane_layout_centers_must_match_lane_count():
    with pytest.raises(ValidationError):
        LaneLayout(lane_count=2, centers_x=[1.0, 2.0, 3.0])


def test_lane_boundaries_span_zero_to_one_match_count_and_keep_min_width():
    assert LaneLayout(lane_count=3).boundaries == []
    assert LaneLayout(lane_count=3, boundaries=[0, 0.3, 0.7, 1]).boundaries[1] == 0.3
    assert LaneLayout(lane_count=1, boundaries=[0, 1])
    for bad in (
        [0, 0.5],            # shorter than lane_count + 1
        [0, 0.2, 0.4, 0.6, 0.8, 1],  # longer than lane_count + 1
        [0.1, 0.5, 1],       # must start at 0
        [0, 0.5, 0.9],       # must end at 1
        [0, 0.5, 0.51, 1],   # interior boundaries closer than 0.02
        [0, 0.7, 0.4, 1],    # must be increasing
        [0, float("nan"), 1],
        [0, -0.1, 1],
    ):
        with pytest.raises(ValidationError):
            LaneLayout(lane_count=3, boundaries=bad)
    with pytest.raises(ValidationError):
        LaneLayout(boundaries=[0, 1])  # boundaries need a lane_count


def test_lane_boundary_min_spacing_relaxes_for_dense_lane_counts():
    LaneLayout(lane_count=51, boundaries=[i / 51 for i in range(52)])
    with pytest.raises(ValidationError):
        LaneLayout(lane_count=51, boundaries=[0, 0.005] + [i / 51 for i in range(2, 52)])
    # lane_count 3 still requires the 0.02 gap
    with pytest.raises(ValidationError):
        LaneLayout(lane_count=3, boundaries=[0, 0.5, 0.515, 1])


def test_crop_rect_must_be_non_empty():
    CropRect(x0=0, y0=0, x1=1, y1=1)
    with pytest.raises(ValidationError):
        CropRect(x0=5, y0=0, x1=5, y1=1)


def source(**over):
    base = {
        "source_asset_id": "IMG001",
        "representation_kind": "presentation_export_candidate",
        "sha256": SHA,
        "size_bytes": 10,
        "image": {"width": 4, "height": 3, "dtype": "uint8", "channels": "RGB"},
        "quant_eligibility": "presentation_only",
    }
    return Source.model_validate({**base, **over})


def test_rgb_uint8_source_can_never_be_quantification_ready():
    source()
    for bad in ("quantification_ready", "unverified"):
        with pytest.raises(ValidationError):
            source(quant_eligibility=bad)


def test_unverified_native_container_is_allowed():
    s = source(
        representation_kind="native_container",
        image=None,
        declared_image_size=[2750, 2200],
        quant_eligibility="unverified",
    )
    assert s.declared_image_size == (2750, 2200)


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.tif"])
def test_source_relative_path_stays_inside_project(path):
    with pytest.raises(ValidationError):
        source(relative_path=path)


def test_sha256_must_be_64_hex():
    with pytest.raises(ValidationError):
        source(sha256="abc")


def test_unknown_keys_round_trip(seed):
    data = copy.deepcopy(seed)
    data["schema_version"] = "1.0"
    data["future_key"] = {"x": [1, 2]}
    data["sources"][0]["extra_note"] = "kept"
    dumped = Project.model_validate(data).model_dump(mode="json")
    assert dumped["future_key"] == {"x": [1, 2]}
    assert dumped["sources"][0]["extra_note"] == "kept"
    for key in ("status", "created_date", "is_executable_project", "review_state"):
        assert dumped[key] == seed[key]


def test_k_and_l_are_separate_regions_with_the_same_label(seed):
    data = copy.deepcopy(seed)
    data["schema_version"] = "1.0"
    p = Project.model_validate(data)
    k = next(r for r in p.membrane_regions if r.user_label == "K")
    l = next(r for r in p.membrane_regions if r.user_label == "L")
    assert k.target_label and k.target_label == l.target_label
    assert k.region_id != l.region_id


def test_duplicate_panel_ids_are_rejected(seed):
    data = copy.deepcopy(seed)
    data["schema_version"] = "1.0"
    data["panels"].append(copy.deepcopy(data["panels"][0]))
    with pytest.raises(ValidationError):
        Project.model_validate(data)


def test_panel_must_refer_to_a_known_region(seed):
    data = copy.deepcopy(seed)
    data["schema_version"] = "1.0"
    data["panels"][0]["region_id"] = "REGION_NOPE"
    with pytest.raises(ValidationError):
        Project.model_validate(data)


def test_old_projects_default_to_no_band_row_candidates(seed):
    data = copy.deepcopy(seed)
    data['schema_version'] = '1.0'
    project = Project.model_validate(data)
    assert all(r.band_row_candidates == [] for r in project.membrane_regions)
    assert all(p.selected_band_row_candidate_id is None for p in project.panels)


def test_band_row_candidates_validate_references():
    from wb.model import BandRowCandidate
    candidate = {
        'candidate_id': 'M1_R1', 'crop_rect_rectified': {'x0': 1, 'y0': 2, 'x1': 10, 'y1': 5},
        'band_row_y_rectified': 3, 'selected_exposure_id': 'CAP_1',
        'exposure_reason': 'checked', 'red_pixels_in_crop_by_capture': {}, 'score': .8,
        'evidence': 'fixture',
    }
    row = BandRowCandidate.model_validate(candidate)
    assert row.crop_rect_rectified.y0 == 2
    data = {
        'project_id': 'TEST', 'captures': [{'capture_id': 'CAP_1'}],
        'membrane_regions': [{'region_id': 'M1', 'user_label': 'M1', 'band_row_candidates': [candidate]}],
        'panels': [{'panel_id': 'P1', 'region_id': 'M1', 'selected_band_row_candidate_id': 'M1_R1'}],
    }
    assert Project.model_validate(data).panels[0].selected_band_row_candidate_id == 'M1_R1'
    bad = copy.deepcopy(data)
    bad['panels'][0]['selected_band_row_candidate_id'] = 'missing'
    with pytest.raises(ValidationError, match='unknown band row'):
        Project.model_validate(bad)
    bad = copy.deepcopy(data)
    bad['membrane_regions'][0]['band_row_candidates'].append(candidate)
    with pytest.raises(ValidationError, match='duplicate band row'):
        Project.model_validate(bad)
    bad = copy.deepcopy(data)
    bad['membrane_regions'][0]['band_row_candidates'][0]['selected_exposure_id'] = 'missing'
    with pytest.raises(ValidationError, match='unknown capture'):
        Project.model_validate(bad)
