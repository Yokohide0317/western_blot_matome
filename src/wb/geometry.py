"""Geometry in image-edge coordinates, with explicit frame identifiers."""

from dataclasses import dataclass
import math

import cv2
import numpy as np


@dataclass(frozen=True, eq=False)
class Affine:
    from_frame_id: str
    to_frame_id: str
    matrix: np.ndarray

    def __post_init__(self):
        matrix = np.array(self.matrix, dtype=np.float64, copy=True)
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or not np.array_equal(matrix[2], [0, 0, 1]):
            raise ValueError("matrix must be a finite 3x3 affine matrix with last row [0, 0, 1]")
        matrix.setflags(write=False)
        object.__setattr__(self, "matrix", matrix)

    def __matmul__(self, other):
        if not isinstance(other, Affine):
            return NotImplemented
        if self.from_frame_id != other.to_frame_id:
            raise ValueError(f"frame mismatch: {self.from_frame_id!r} != {other.to_frame_id!r}")
        return Affine(other.from_frame_id, self.to_frame_id, self.matrix @ other.matrix)

    def inverse(self):
        try:
            matrix = np.linalg.inv(self.matrix)
        except np.linalg.LinAlgError as exc:
            raise ValueError("cannot invert singular affine transform") from exc
        matrix[2] = [0, 0, 1]
        return Affine(self.to_frame_id, self.from_frame_id, matrix)

    def apply(self, points):
        points = np.asarray(points, dtype=np.float64)
        if points.ndim not in (1, 2) or points.shape[-1:] != (2,):
            raise ValueError("points must have shape (2,) or (N, 2)")
        return points @ self.matrix[:2, :2].T + self.matrix[:2, 2]

    def to_json(self, transform_id, kind, status, revision, evidence=None):
        return {"transform_id": transform_id, "kind": kind,
                "from_frame_id": self.from_frame_id, "to_frame_id": self.to_frame_id,
                "matrix": self.matrix.tolist(), "status": status,
                "evidence": evidence, "revision": revision}

    @classmethod
    def from_json(cls, record):
        return cls(record["from_frame_id"], record["to_frame_id"], record["matrix"])

    @classmethod
    def identity(cls, frame_a, frame_b=None):
        return cls(frame_a, frame_a if frame_b is None else frame_b, np.eye(3))

    @classmethod
    def translation(cls, dx, dy, from_frame_id, to_frame_id):
        return cls(from_frame_id, to_frame_id, [[1, 0, dx], [0, 1, dy], [0, 0, 1]])

    @classmethod
    def scaling(cls, s, from_frame_id, to_frame_id):
        return cls(from_frame_id, to_frame_id, [[s, 0, 0], [0, s, 0], [0, 0, 1]])

    @classmethod
    def rotation_about(cls, center_xy, degrees, from_frame_id, to_frame_id):
        """Positive degrees rotate clockwise visually in image coordinates (y down)."""
        x, y = center_xy
        angle = math.radians(degrees)
        c, s = math.cos(angle), math.sin(angle)
        if degrees % 90 == 0:
            c, s = ((1, 0), (0, 1), (-1, 0), (0, -1))[int(degrees // 90) % 4]
        return cls(from_frame_id, to_frame_id,
                   [[c, -s, x - c*x + s*y], [s, c, y - s*x - c*y], [0, 0, 1]])


def pixel_center(row, col):
    return (col + 0.5, row + 0.5)


def to_opencv_matrix(m_edge):
    m = np.asarray(m_edge, dtype=np.float64)
    if m.shape != (3, 3):
        raise ValueError("matrix must have shape (3, 3)")
    plus = np.array([[1, 0, .5], [0, 1, .5], [0, 0, 1]])
    minus = np.array([[1, 0, -.5], [0, 1, -.5], [0, 0, 1]])
    return minus @ m @ plus


def warp(image, t_dst_from_src: Affine, out_width: int, out_height: int,
         interpolation=cv2.INTER_LINEAR, border_value=0):
    return cv2.warpAffine(image, to_opencv_matrix(t_dst_from_src.matrix)[:2],
                          (out_width, out_height), flags=interpolation,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border_value)


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.x0, self.y0, self.x1, self.y1)) or not (self.x1 > self.x0 and self.y1 > self.y0):
            raise ValueError("rectangle must have finite coordinates and positive width and height")

    def corners(self):
        return np.array([[self.x0, self.y0], [self.x1, self.y0],
                         [self.x1, self.y1], [self.x0, self.y1]], dtype=np.float64)


class OutOfFrameError(ValueError):
    pass


def polygon_within_frame(polygon, width, height):
    points = np.asarray(polygon, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) == 0:
        raise ValueError("polygon must have shape (N, 2) with N > 0")
    return bool(np.isfinite(points).all() and (points[:, 0] >= 0).all() and
                (points[:, 0] <= width).all() and (points[:, 1] >= 0).all() and
                (points[:, 1] <= height).all())


def require_within_frame(polygon, width, height):
    if not polygon_within_frame(polygon, width, height):
        raise OutOfFrameError(f"polygon extends outside frame {width} x {height}")


@dataclass(frozen=True)
class PanelGeometry:
    transform: Affine
    out_width: int
    out_height: int
    scale: float
    content_height: float


def _panel_geometry(a_e, d_r, crop, scale, out_width, panel_frame_id):
    crop_frame = f"{panel_frame_id}:crop"
    c_p = Affine.translation(-crop.x0, -crop.y0, d_r.to_frame_id, crop_frame)
    s_p = Affine.scaling(scale, crop_frame, panel_frame_id)
    transform = s_p @ c_p @ d_r @ a_e
    content_height = (crop.y1 - crop.y0) * scale
    return PanelGeometry(transform, out_width, math.ceil(content_height), scale, content_height)


def panel_transform(a_e: Affine, d_r: Affine, crop: Rect, target_width_px: int, panel_frame_id: str):
    if isinstance(target_width_px, bool) or not isinstance(target_width_px, (int, np.integer)) or target_width_px <= 0:
        raise ValueError("target_width_px must be a positive integer")
    scale = target_width_px / (crop.x1 - crop.x0)
    return _panel_geometry(a_e, d_r, crop, scale, int(target_width_px), panel_frame_id)


def crop_polygon_in_source(a_e, d_r, crop):
    return (a_e.inverse() @ d_r.inverse()).apply(crop.corners())


def fit_lane_centers(lane_count: int, known: dict[int, float]) -> list[float]:
    if isinstance(lane_count, bool) or not isinstance(lane_count, int) or lane_count < 1:
        raise ValueError("lane_count must be a positive integer")
    if len(known) < 2:
        raise ValueError("at least two known lane centers are required")
    if any(isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= lane_count for k in known):
        raise ValueError("lane numbers must be integers within lane_count")
    x = np.array(list(known), dtype=np.float64)
    y = np.array(list(known.values()), dtype=np.float64)
    if not np.isfinite(y).all():
        raise ValueError("lane centers must be finite")
    pitch = np.dot(x - x.mean(), y - y.mean()) / np.dot(x - x.mean(), x - x.mean())
    return (y.mean() + pitch * (np.arange(1, lane_count + 1) - x.mean())).tolist()


def lane_crop_x(centers, pitch=None):
    values = np.asarray(centers, dtype=np.float64)
    if values.ndim != 1 or len(values) == 0 or not np.isfinite(values).all():
        raise ValueError("centers must be a nonempty finite sequence")
    if pitch is None:
        if len(values) < 2:
            raise ValueError("at least two centers or an explicit pitch are required")
        pitch = (values[-1] - values[0]) / (len(values) - 1)
    if not math.isfinite(pitch) or pitch <= 0:
        raise ValueError("pitch must be positive and finite")
    return (float(values[0] - pitch / 2), float(values[-1] + pitch / 2))


def lane_spacing_residuals(centers):
    values = np.asarray(centers, dtype=np.float64)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError("at least two finite centers are required")
    fitted = fit_lane_centers(len(values), dict(enumerate(values, 1)))
    return (values - fitted).tolist()


def marker_panel_transform(a_mr: Affine, d_r: Affine, crop: Rect, scale: float, panel_frame_id: str):
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be positive and finite")
    return _panel_geometry(a_mr, d_r, crop, scale,
                           math.ceil((crop.x1 - crop.x0) * scale), panel_frame_id)


def marker_x_span(rectified_polygon, y0: float, y1: float) -> tuple[float, float]:
    points = np.asarray(rectified_polygon, dtype=np.float64)
    # Clip polygon against horizontal slab [y0, y1] so rotated tall marker ROIs keep true local width.
    def clip_half(pts, keep, intersect):
        if len(pts) == 0:
            return pts
        out = []
        prev = pts[-1]
        prev_in = keep(prev)
        for curr in pts:
            curr_in = keep(curr)
            if curr_in != prev_in:
                out.append(intersect(prev, curr))
            if curr_in:
                out.append(curr)
            prev, prev_in = curr, curr_in
        return np.asarray(out, dtype=np.float64) if out else np.empty((0, 2), dtype=np.float64)

    clipped = clip_half(
        points,
        lambda p: p[1] >= y0 - 1e-9,
        lambda a, b: a + (b - a) * ((y0 - a[1]) / (b[1] - a[1])),
    )
    clipped = clip_half(
        clipped,
        lambda p: p[1] <= y1 + 1e-9,
        lambda a, b: a + (b - a) * ((y1 - a[1]) / (b[1] - a[1])),
    )
    target = clipped if len(clipped) >= 2 and float(clipped[:, 0].max() - clipped[:, 0].min()) > 1e-6 else points
    return float(target[:, 0].min()), float(target[:, 0].max())
