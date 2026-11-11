"""pydantic models for project.json (docs/design.md).

Records that come from the seed or from ingest keep unknown keys (extra="allow")
so a round trip never drops data. Records that this tool defines itself are
closed (extra="forbid") so a typo in a field name fails loudly.
"""

from __future__ import annotations

import math
from pathlib import PurePath
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

BandStatus = Literal["not_reviewed", "detected", "not_detected", "ambiguous"]
ReviewStatus = Literal["draft", "proposed", "confirmed", "rejected"]
QuantificationStatus = Literal["not_requested", "blocked", "ready", "computed", "invalid"]
CreatedBy = Literal["human", "algorithm", "agent"]
Purpose = Literal["band", "measurement_roi", "background_roi"]
SampleRole = Literal["sample", "positive_control", "negative_control", "unknown"]
TransformStatus = Literal["candidate", "confirmed"]
TransformKind = Literal[
    "exposure_to_reference",
    "reference_to_rectified",
    "marker_to_reference",
    "analysis_to_presentation",
]
# "filename_and_exposure_matched_geometry_unverified" is the seed's value for a
# filename match; it is a candidate with an explicit reason, so it is kept as is.
CaptureLinkStatus = Literal[
    "filename_and_exposure_matched_geometry_unverified", "candidate", "verified"
]
RegionAssignmentStatus = Literal["unassigned", "candidate", "proposed", "confirmed"]
TargetAssignmentStatus = Literal["unresolved", "candidate", "confirmed"]
GroupingStatus = Literal["candidate", "confirmed"]
AlignmentStatus = Literal["unreviewed", "candidate", "confirmed"]
MarkerRegistrationStatus = Literal["unreviewed", "pending", "candidate", "confirmed"]
LaneLayoutStatus = Literal["unreviewed", "candidate", "proposed", "confirmed"]
DisplayOrderStatus = Literal["initial_from_user_mapping_not_final", "confirmed"]
RuleStatus = Literal["awaiting_region_assignment", "assigned", "confirmed"]
RepresentationKind = Literal[
    "presentation_export_candidate", "native_container", "analysis_candidate", "analysis_export_candidate"
]
QuantEligibility = Literal["presentation_only", "unverified", "quantification_ready"]
OutputState = Literal["draft", "final"]
FlagTargetKind = Literal[
    "annotation", "panel", "transform", "region", "image_set", "marker_registration"
]
FlagReason = Literal["source_hash_changed", "source_size_changed"]

Coord = Annotated[float, Field(allow_inf_nan=False)]
Point = Annotated[list[Coord], Field(min_length=2, max_length=2)]


def _check_matrix(m: list[list[float]]) -> list[list[float]]:
    if len(m) != 3 or any(len(row) != 3 for row in m):
        raise ValueError("matrix must be 3x3")
    if list(m[2]) != [0, 0, 1]:
        raise ValueError("last row of an affine matrix must be [0, 0, 1]")
    return m


Matrix3 = Annotated[list[list[Coord]], AfterValidator(_check_matrix)]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class CoordinateConvention(_Open):
    origin: Literal["top_left_outer_edge"] = "top_left_outer_edge"
    axes: Literal["x_right_y_down"] = "x_right_y_down"
    unit: str = "full_resolution_px"
    pixel_center: Literal["col+0.5,row+0.5"] = "col+0.5,row+0.5"
    matrix_storage: Literal["row_major"] = "row_major"
    vector_convention: Literal["column_vector"] = "column_vector"
    matrix_direction: str = "from_frame_id_to_to_frame_id"


class InputArchive(_Open):
    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImageInfo(_Open):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    dtype: str
    channels: str
    page_count: int | None = None
    orientation: str | int | None = None


def _check_relative(p: str | None) -> str | None:
    if p is not None:
        path = PurePath(p)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay inside the project directory")
    return p


class Source(_Open):
    source_asset_id: str
    representation_kind: RepresentationKind
    archive_member: str | None = None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    relative_path: Annotated[str | None, AfterValidator(_check_relative)] = None
    image: ImageInfo | None = None
    declared_image_size: tuple[int, int] | None = None
    pixel_decode_performed: bool | None = None
    quant_eligibility: QuantEligibility = "unverified"
    red_overlay_present: bool | None = None
    native_source_asset_id: str | None = None

    @model_validator(mode="after")
    def _rgb8_is_presentation_only(self) -> Source:
        img = self.image
        if img and img.dtype == "uint8" and img.channels.upper().startswith("RGB"):
            if self.quant_eligibility != "presentation_only":
                raise ValueError("8-bit RGB sources are presentation_only")
        return self


class Frame(_Open):
    frame_id: str
    source_asset_id: str | None = None
    page_index: int | None = None
    channel_selection: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    coordinate_space: str | None = None


class Capture(_Open):
    capture_id: str
    presentation_source_asset_id: str | None = None
    native_source_asset_id: str | None = None
    analysis_source_asset_id: str | None = None
    link_status: CaptureLinkStatus = "candidate"
    link_evidence: str | None = None
    exposure_s: float | None = None
    exposure_source: str | None = None
    modality: str | None = None
    acquisition_mode: str | None = None
    analysis_to_presentation_transform_id: str | None = None


class ImageSet(_Open):
    image_set_id: str
    grouping_source: str | None = None
    grouping_status: GroupingStatus = "candidate"
    reference_frame_id: str | None = None
    geometry_alignment_status: AlignmentStatus = "unreviewed"
    capture_ids: list[str] = []
    marker_source_candidate_ids: list[str] = []
    marker_registration_status: MarkerRegistrationStatus = "unreviewed"


class Transform(_Closed):
    transform_id: str
    kind: TransformKind
    from_frame_id: str
    to_frame_id: str
    matrix: Matrix3
    status: TransformStatus = "candidate"
    evidence: Any = None
    revision: int = Field(default=1, ge=1)


class FramePolygon(_Closed):
    frame_id: str
    polygon: Annotated[list[Point], Field(min_length=3)]


class CropRect(_Closed):
    x0: Coord
    y0: Coord
    x1: Coord
    y1: Coord

    @model_validator(mode="after")
    def _non_empty(self) -> CropRect:
        if not (self.x1 > self.x0 and self.y1 > self.y0):
            raise ValueError("crop_rect must have x1 > x0 and y1 > y0")
        return self


class BandRowCandidate(_Closed):
    candidate_id: str = Field(min_length=1)
    crop_rect_rectified: CropRect
    band_row_y_rectified: Coord
    selected_exposure_id: str
    exposure_reason: str
    red_pixels_in_crop_by_capture: dict[str, int] = Field(default_factory=dict)
    score: Coord
    evidence: Any = None


class MembraneRegion(_Open):
    included: bool = True
    suspended_panel_ids: list[str] = Field(default_factory=list)
    region_id: str
    user_label: str
    target_label: str | None = None
    target_assignment_source: str | None = None
    target_assignment_status: TargetAssignmentStatus = "unresolved"
    physical_membrane_id: str | None = None
    image_set_id: str | None = None
    reference_frame_id: str | None = None
    geometry: FramePolygon | None = None
    lane_count: int | None = Field(default=None, ge=1)
    band_row_candidates: list[BandRowCandidate] = Field(default_factory=list)
    lane_annotations: list[dict[str, Any]] = []
    region_assignment_status: RegionAssignmentStatus = "unassigned"


class LaneLayout(_Closed):
    lane_count: int | None = Field(default=None, ge=1)
    centers_x: list[Coord] = []
    boundaries: list[float] = []
    # Sample intervals in crop-width fractions. Gaps are not sample lanes.
    spans: list[tuple[float, float]] = []
    status: LaneLayoutStatus = "unreviewed"

    @model_validator(mode="after")
    def _centers_match_count(self) -> LaneLayout:
        if self.centers_x and self.lane_count is not None and len(self.centers_x) != self.lane_count:
            raise ValueError("centers_x must have lane_count entries")
        if self.spans:
            if self.lane_count is None or len(self.spans) != self.lane_count:
                raise ValueError("lane spans must have lane_count entries")
            previous = 0.0
            for start, end in self.spans:
                if not all(math.isfinite(x) for x in (start, end)) or not 0 <= start < end <= 1 or start < previous:
                    raise ValueError("lane spans must be ordered, nonoverlapping fractions within 0..1")
                previous = end
        if self.boundaries:
            n = self.lane_count
            if n is None or len(self.boundaries) != n + 1 or self.boundaries[0] != 0 or self.boundaries[-1] != 1:
                raise ValueError("lane boundaries must start at 0, end at 1 and match lane_count")
            if any(not math.isfinite(x) or x < 0 or x > 1 for x in self.boundaries):
                raise ValueError("lane boundaries must be finite fractions")
            minimum = min(0.02, 0.5 / n)
            if any(b - a < minimum - 1e-9 for a, b in zip(self.boundaries, self.boundaries[1:])):
                raise ValueError("lane boundaries must increase with minimum width")
        return self


class MarkerXRange(_Closed):
    x0: Coord
    x1: Coord

    @model_validator(mode="after")
    def _non_empty(self) -> MarkerXRange:
        if not self.x1 > self.x0:
            raise ValueError("marker x range must have x1 > x0")
        return self


class DisplayGeometry(_Closed):
    reference_frame_id: str
    rectified_frame_id: str | None = None
    reference_to_rectified_transform_id: str | None = None
    crop_rect: CropRect | None = None
    target_width_px: int | None = Field(default=None, gt=0)
    lane_layout: LaneLayout = Field(default_factory=LaneLayout)
    # Hand-set horizontal range of the colorimetric marker, in the same rectified
    # frame as crop_rect. The marker always uses crop_rect's vertical range.
    marker_x: MarkerXRange | None = None
    geometry_revision: int = Field(default=1, ge=1)


class Panel(_Open):
    panel_id: str
    region_id: str
    included: bool = True
    label: str | None = None
    image_set_id: str | None = None
    display_order: int | None = None
    display_order_status: DisplayOrderStatus = "initial_from_user_mapping_not_final"
    sample_group_id: str | None = None
    selected_exposure_id: str | None = None
    exposure_reason: str | None = None
    selected_band_row_candidate_id: str | None = None
    display_geometry: DisplayGeometry | None = None
    band_annotation_ids: list[str] = []
    background_roi_ids: list[str] = []
    control_for_panel_ids: list[str] = []
    marker_registration_id: str | None = None
    review_status: ReviewStatus = "draft"


class AnnotationGeometry(_Closed):
    type: Literal["point", "polygon"]
    coordinates: list[Point]

    @model_validator(mode="after")
    def _shape(self) -> AnnotationGeometry:
        n = len(self.coordinates)
        if self.type == "point" and n != 1:
            raise ValueError("a point has exactly one coordinate")
        if self.type == "polygon" and n < 3:
            raise ValueError("a polygon needs at least three coordinates")
        return self


class Annotation(_Closed):
    annotation_id: str
    frame_id: str
    panel_id: str
    lane_id: int | str | None = None
    purpose: Purpose
    geometry: AnnotationGeometry
    created_by: CreatedBy
    method: str | None = None
    parameters: dict[str, Any] | None = None
    confidence: float | None = None
    evidence: Any = None
    band_status: BandStatus = "not_reviewed"
    review_status: ReviewStatus = "draft"
    quantification_status: QuantificationStatus = "not_requested"
    revision: int = Field(default=1, ge=1)


class MarkerRegistration(_Closed):
    marker_registration_id: str
    membrane_region_id: str
    transform_id: str | None = None
    marker_roi: FramePolygon | None = None
    status: Literal["pending", "candidate", "confirmed"] = "pending"


class Sample(_Closed):
    name: str | None = None  # None = an empty column in the sample table
    role: SampleRole = "unknown"


class SampleGroup(_Closed):
    sample_group_id: str
    lane_count: int | None = Field(default=None, ge=1)
    samples: list[Sample] = []

    @model_validator(mode="after")
    def _samples_match_lanes(self) -> SampleGroup:
        if self.samples and self.lane_count is not None and len(self.samples) != self.lane_count:
            raise ValueError("samples are in lane order and must have lane_count entries")
        return self


class UnassignedTargetRule(_Open):
    rule_id: str
    description: str | None = None
    target_label: str
    assignment_source: str | None = None
    target_rule_confirmed: bool = False
    region_assignments: list[str] = []
    control_for_panel_ids: list[str] = []
    status: RuleStatus = "awaiting_region_assignment"


class HistoryEntry(_Closed):
    operation: str
    project_revision: int
    timestamp: str
    summary: str = ""


class OutputRecord(_Closed):
    kind: str
    path: str
    state: OutputState = "draft"
    selected_exposure_ids: dict[str, str | None] = {}
    geometry_revisions: dict[str, int | None] = {}


class ReviewFlag(_Closed):
    """Marks geometry that depended on a source whose bytes changed."""

    target_kind: FlagTargetKind
    target_id: str
    source_asset_id: str
    reason: FlagReason
    detail: str | None = None


class ReviewState(_Open):
    selected_panel_id: str | None = None
    schema_notes: str | None = None


def _unique(ids: list[str], what: str) -> None:
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {what} ids")


class QuantificationLane(_Closed):
    lane_id: int = Field(gt=0, strict=True)
    x: int = Field(ge=0, strict=True)
    y: int = Field(ge=0, strict=True)
    background_x: int = Field(ge=0, strict=True)
    background_y: int = Field(ge=0, strict=True)
    excluded: bool = False


class QuantificationGroup(_Open):
    group_id: str
    panel_id: str
    capture_id: str
    source_asset_id: str
    frame_id: str
    width_px: int = Field(gt=0, strict=True)
    height_px: int = Field(gt=0, strict=True)
    signal_polarity: Literal['high', 'low'] = 'high'
    source_confirmed: bool = False
    saturation_value: int | None = Field(default=None, ge=0, le=65535, strict=True)
    lanes: list[QuantificationLane]
    results: dict[str, Any] | None = None

    @model_validator(mode='after')
    def unique_lanes(self):
        if not self.lanes or len({l.lane_id for l in self.lanes}) != len(self.lanes):
            raise ValueError('Unique nonempty lanes required')
        return self


class EditHistory(_Closed):
    undo: list[Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]] = Field(default_factory=list, max_length=50)
    redo: list[Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]] = Field(default_factory=list, max_length=50)


class Project(_Open):
    schema_version: Literal["1.0"] = "1.0"
    project_id: str
    quantification_groups: list[QuantificationGroup] = Field(default_factory=list)
    project_revision: int = Field(default=1, ge=1)
    edit_history: EditHistory = Field(default_factory=EditHistory)
    status: str | None = None
    figure_scale_mode: Literal["equal_lane_width"] = "equal_lane_width"
    created_date: str | None = None
    is_executable_project: bool | None = None
    coordinate_convention: CoordinateConvention = Field(default_factory=CoordinateConvention)
    input_archive: InputArchive | None = None
    sources: list[Source] = []
    frames: list[Frame] = []
    captures: list[Capture] = []
    image_sets: list[ImageSet] = []
    transforms: list[Transform] = []
    membrane_regions: list[MembraneRegion] = []
    panels: list[Panel] = []
    annotations: list[Annotation] = []
    marker_registrations: list[MarkerRegistration] = []
    sample_groups: list[SampleGroup] = []
    unassigned_target_rules: list[UnassignedTargetRule] = []
    unresolved_items: list[str] = []
    review_state: ReviewState | None = None
    review_flags: list[ReviewFlag] = []
    history: list[HistoryEntry] = []
    outputs: list[OutputRecord] = []
    migrated_from: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_membrane_inclusion(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        panels = [dict(p) if isinstance(p, dict) else p for p in value.get("panels", [])]
        regions = []
        for original in value.get("membrane_regions", []):
            if not isinstance(original, dict) or "included" in original:
                regions.append(original)
                continue
            region = dict(original)
            members = [p for p in panels if isinstance(p, dict) and p.get("region_id") == region["region_id"]]
            region["included"] = not members or any(p.get("included", True) for p in members)
            restore = set(region.pop("suspended_panel_ids", []))
            for panel in members:
                if panel.get("panel_id") in restore:
                    panel["included"] = True
            regions.append(region)
        value.update(panels=panels, membrane_regions=regions)
        return value

    def included_panels(self):
        enabled = {r.region_id for r in self.membrane_regions if r.included}
        return [p for p in self.panels if p.included and p.region_id in enabled]

    @field_validator("figure_scale_mode", mode="before")
    @classmethod
    def _migrate_figure_scale(cls, value):
        # Older projects stored a selectable full-row width. All views and
        # exports now use the same lane scale, including those projects.
        return "equal_lane_width" if value == "equal_width" else value

    @model_validator(mode="after")
    def _references(self) -> Project:
        _unique([s.source_asset_id for s in self.sources], "source")
        _unique([f.frame_id for f in self.frames], "frame")
        _unique([c.capture_id for c in self.captures], "capture")
        _unique([s.image_set_id for s in self.image_sets], "image_set")
        _unique([t.transform_id for t in self.transforms], "transform")
        _unique([r.region_id for r in self.membrane_regions], "region")
        _unique([p.panel_id for p in self.panels], "panel")
        _unique([a.annotation_id for a in self.annotations], "annotation")
        _unique([g.sample_group_id for g in self.sample_groups], "sample_group")
        regions = {r.region_id for r in self.membrane_regions}
        panels = {p.panel_id for p in self.panels}
        captures = {c.capture_id for c in self.captures}
        regions_by_id = {r.region_id: r for r in self.membrane_regions}
        for region in self.membrane_regions:
            _unique([c.candidate_id for c in region.band_row_candidates], "band row candidate")
            for candidate in region.band_row_candidates:
                if candidate.selected_exposure_id not in captures:
                    raise ValueError(f"candidate {candidate.candidate_id} selects unknown capture")
        for p in self.panels:
            if p.region_id not in regions:
                raise ValueError(f"panel {p.panel_id} refers to unknown region {p.region_id}")
            if p.selected_exposure_id is not None and p.selected_exposure_id not in captures:
                raise ValueError(f"panel {p.panel_id} selects unknown capture")
            if p.selected_band_row_candidate_id is not None:
                candidates = regions_by_id[p.region_id].band_row_candidates
                if p.selected_band_row_candidate_id not in {c.candidate_id for c in candidates}:
                    raise ValueError(f"panel {p.panel_id} selects unknown band row candidate")
        for a in self.annotations:
            if a.panel_id not in panels:
                raise ValueError(f"annotation {a.annotation_id} refers to unknown panel")
        return self
