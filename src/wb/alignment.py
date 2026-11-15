"""Core numerical band alignment and inspection for western blot panels."""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

from wb.geometry import Affine, warp


def _red_display_mask(image: np.ndarray) -> np.ndarray:
    """Return a boolean mask of ChemiDoc red saturation display pixels."""
    if image.ndim != 3 or image.shape[2] < 3:
        return np.zeros(image.shape[:2], dtype=bool)
    rgb = image[..., :3]
    if np.issubdtype(rgb.dtype, np.floating):
        scale = 255.0 if float(np.max(rgb)) <= 1.0 else 1.0
        r = rgb[..., 0] * scale
        g = rgb[..., 1] * scale
        b = rgb[..., 2] * scale
    else:
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (r > 240) & (g < 20) & (b < 20)


def extract_band_signal(image: np.ndarray) -> np.ndarray:
    """Convert an RGB or grayscale blot image into a background-subtracted signal in [0, 1].

    Dark bands (and red ChemiDoc saturation markers) become positive signal,
    while uneven illumination / membrane background is removed via Gaussian
    difference filtering.
    """
    arr = np.asarray(image)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        rgb = arr[..., :3]
        red_mask = _red_display_mask(rgb)
        if np.issubdtype(rgb.dtype, np.floating):
            rgb_u8 = np.clip(rgb * (255.0 if float(np.max(rgb)) <= 1.0 else 1.0), 0, 255).astype(np.uint8)
        else:
            rgb_u8 = np.clip(rgb, 0, 255).astype(np.uint8)
        gray = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        darkness = 1.0 - gray
        darkness[red_mask] = 1.0
    elif arr.ndim == 2:
        if np.issubdtype(arr.dtype, np.floating):
            max_val = float(np.max(arr)) if arr.size else 0.0
            norm = arr.astype(np.float32) / (255.0 if max_val > 1.0 else 1.0)
            darkness = 1.0 - np.clip(norm, 0.0, 1.0)
        else:
            darkness = (255.0 - np.clip(arr, 0, 255).astype(np.float32)) / 255.0
    else:
        raise ValueError(f"Expected 2D grayscale or 3D RGB image, got shape {arr.shape}")

    smooth = cv2.GaussianBlur(darkness, (0, 0), 1.5)
    background = cv2.GaussianBlur(smooth, (0, 0), 30.0)
    residual = np.clip(smooth - background, 0.0, 1.0)
    return residual.astype(np.float32)


def _crop_to_mask(
    signal: np.ndarray, mask: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Crop signal and boolean mask to the active mask bounding box."""
    h, w = signal.shape[:2]
    if mask is None:
        return signal, np.ones((h, w), dtype=bool), 0, 0
    bool_mask = np.asarray(mask, dtype=bool)
    ys, xs = np.nonzero(bool_mask)
    if ys.size == 0:
        return signal[:0, :0], bool_mask[:0, :0], 0, 0
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    return signal[y0:y1, x0:x1], bool_mask[y0:y1, x0:x1], x0, y0


def _detect_vertical_smears(
    sig_roi: np.ndarray, mask_roi: np.ndarray, thresh: float
) -> tuple[bool, int, int]:
    """Return (is_smear_dominated, smear_area, band_area) for vertical-smear guarding."""
    binary = ((sig_roi > thresh) & mask_roi).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    smear_area = 0
    band_area = 0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < 15:
            continue
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        if h > 1.6 * w and h > 20:
            smear_area += area
        elif w >= 1.2 * h or h <= 20:
            band_area += area
    # Also check directional gradient ratio inside active region
    gx = cv2.Sobel(sig_roi, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(sig_roi, cv2.CV_32F, 0, 1, ksize=3)
    active = mask_roi & (sig_roi > thresh * 0.5)
    if np.count_nonzero(active) >= 20:
        ex = float(np.mean(gx[active] ** 2))
        ey = float(np.mean(gy[active] ** 2))
    else:
        ex, ey = 0.0, 0.0
    smear_dominated = (smear_area > 0 and smear_area >= 2 * max(band_area, 1)) or (
        smear_area > band_area and ex > 1.8 * max(ey, 1e-9)
    )
    return smear_dominated, smear_area, band_area


def _estimate_centroid_regression(
    sig_roi: np.ndarray,
    mask_roi: np.ndarray,
    thresh: float,
    max_angle_deg: float,
    y_offset: int,
) -> tuple[float | None, float, list[dict[str, Any]]]:
    """Estimate tilt via adaptive-thresholded band-row weighted linear regression and PCA."""
    roi_h, roi_w = sig_roi.shape
    binary = ((sig_roi > thresh) & mask_roi).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)

    components: list[dict[str, Any]] = []
    pca_angles: list[tuple[float, float]] = []

    min_span = max(40.0, 0.25 * roi_w)

    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < 15:
            continue
        x, y, w, h = (
            int(stats[i, cv2.CC_STAT_LEFT]),
            int(stats[i, cv2.CC_STAT_TOP]),
            int(stats[i, cv2.CC_STAT_WIDTH]),
            int(stats[i, cv2.CC_STAT_HEIGHT]),
        )
        # Skip tall vertical smears
        if h > 1.6 * w and h > 20:
            continue

        comp_mask = labels[y : y + h, x : x + w] == i
        yy, xx = np.nonzero(comp_mask)
        weights = sig_roi[y + yy, x + xx].astype(np.float64)
        total_w = float(np.sum(weights))
        if total_w <= 1e-8:
            continue
        px = (x + xx).astype(np.float64) + 0.5
        py = (y + yy).astype(np.float64) + 0.5
        cx = float(np.sum(px * weights) / total_w)
        cy = float(np.sum(py * weights) / total_w)
        peak_val = float(np.max(weights))
        mean_val = float(np.mean(weights))

        components.append(
            {
                "cx": cx,
                "cy": cy,
                "weight": total_w,
                "x0": float(x),
                "x1": float(x + w),
                "h": float(h),
                "peak": peak_val,
                "mean": mean_val,
            }
        )

        # Single elongated horizontal band -> weighted PCA
        if w >= min_span and w >= 2.5 * max(h, 1):
            dx = px - cx
            dy = py - cy
            cxx = float(np.sum(weights * dx * dx) / total_w)
            cyy = float(np.sum(weights * dy * dy) / total_w)
            cxy = float(np.sum(weights * dx * dy) / total_w)
            cov = np.array([[cxx, cxy], [cxy, cyy]], dtype=np.float64)
            vals, vecs = np.linalg.eigh(cov)
            if vals[1] > 2.5 * max(vals[0], 1e-9):
                vx, vy = vecs[:, 1]
                ang = math.degrees(math.atan2(vy, vx))
                # Normalize to (-90, 90]
                if ang > 90.0:
                    ang -= 180.0
                elif ang <= -90.0:
                    ang += 180.0
                if abs(ang) <= max_angle_deg:
                    pca_angles.append((-ang, total_w))

    # Group components into horizontal rows by y-proximity
    row_gap = max(25.0, min(40.0, 0.06 * roi_h))
    groups: list[list[dict[str, Any]]] = []
    for comp in sorted(components, key=lambda c: c["cy"]):
        if not groups or comp["cy"] - groups[-1][-1]["cy"] > row_gap:
            groups.append([])
        groups[-1].append(comp)

    row_angles: list[tuple[float, float]] = []
    residuals: list[float] = []
    band_rows: list[dict[str, Any]] = []

    for grp in groups:
        xs = np.array([c["cx"] for c in grp], dtype=np.float64)
        ys = np.array([c["cy"] for c in grp], dtype=np.float64)
        ws = np.array([c["weight"] for c in grp], dtype=np.float64)
        x_span = float(max(c["x1"] for c in grp) - min(c["x0"] for c in grp))
        c_span = float(np.ptp(xs)) if len(xs) >= 2 else 0.0

        row_summary = {
            "y": float(np.average(ys, weights=ws) + y_offset),
            "height_px": float(np.median([c["h"] for c in grp])),
            "peak_signal": float(max(c["peak"] for c in grp)),
            "mean_signal": float(np.mean([c["mean"] for c in grp])),
            "x_span_px": x_span,
            "component_count": len(grp),
        }

        if len(grp) >= 2 and (c_span >= min_span or x_span >= min_span):
            # Weighted linear regression y = m*x + b
            w_norm = ws / np.sum(ws)
            mx = float(np.sum(w_norm * xs))
            my = float(np.sum(w_norm * ys))
            denom = float(np.sum(w_norm * (xs - mx) ** 2))
            if denom > 1e-6:
                slope = float(np.sum(w_norm * (xs - mx) * (ys - my)) / denom)
                ang_deg = math.degrees(math.atan(slope))
                if abs(ang_deg) <= max_angle_deg:
                    pred = slope * xs + (my - slope * mx)
                    rms = float(np.sqrt(np.sum(w_norm * (ys - pred) ** 2)))
                    row_angles.append((-ang_deg, float(np.sum(ws))))
                    residuals.append(rms)
                    band_rows.append(row_summary)
        elif len(grp) == 1 and x_span >= min_span:
            band_rows.append(row_summary)

    all_estimates = row_angles + pca_angles
    if not all_estimates:
        return None, 0.0, band_rows

    angs = np.array([a for a, _ in all_estimates], dtype=np.float64)
    wts = np.array([w for _, w in all_estimates], dtype=np.float64)
    # Weighted median / trimmed mean for robustness
    order = np.argsort(angs)
    angs_s, wts_s = angs[order], wts[order]
    cum = np.cumsum(wts_s) / np.sum(wts_s)
    med_idx = int(np.searchsorted(cum, 0.5))
    delta_deg = float(angs_s[min(med_idx, len(angs_s) - 1)])
    mean_res = float(np.mean(residuals)) if residuals else 0.0
    return delta_deg, mean_res, band_rows


def _projection_sharpness(
    sig_roi: np.ndarray, mask_u8: np.ndarray, angle_deg: float
) -> float:
    """Compute horizontal projection sharpness of sig_roi rotated by angle_deg."""
    h, w = sig_roi.shape
    center = (w * 0.5, h * 0.5)
    t = Affine.rotation_about(center, angle_deg, "src", "dst")
    rot_sig = warp(sig_roi, t, w, h, interpolation=cv2.INTER_LINEAR, border_value=0.0)
    rot_msk = warp(mask_u8, t, w, h, interpolation=cv2.INTER_NEAREST, border_value=0)

    counts = np.sum(rot_msk > 0, axis=1).astype(np.float32)
    valid_rows = counts >= max(10.0, 0.30 * w)
    if np.count_nonzero(valid_rows) < 5:
        return 0.0

    sums = np.sum(rot_sig * (rot_msk > 0), axis=1).astype(np.float32)
    profile = sums[valid_rows] / counts[valid_rows]
    dp = np.diff(profile)
    return float(np.var(profile) + 2.0 * np.mean(dp * dp))


def _estimate_projection_sharpness(
    sig_roi: np.ndarray, mask_roi: np.ndarray, max_angle_deg: float
) -> tuple[float, float]:
    """Two-stage coarse-to-fine search maximizing horizontal projection sharpness."""
    h, w = sig_roi.shape
    # Downsample very large ROIs for speed during search
    scale = 1.0
    if max(h, w) > 600:
        scale = 600.0 / max(h, w)
        new_w = max(16, int(round(w * scale)))
        new_h = max(16, int(round(h * scale)))
        sig_s = cv2.resize(sig_roi, (new_w, new_h), interpolation=cv2.INTER_AREA)
        msk_s = cv2.resize(mask_roi.astype(np.uint8), (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    else:
        sig_s = sig_roi
        msk_s = mask_roi.astype(np.uint8)

    coarse_angles = np.arange(-max_angle_deg, max_angle_deg + 1e-6, 0.5)
    coarse_scores = np.array(
        [_projection_sharpness(sig_s, msk_s, float(a)) for a in coarse_angles],
        dtype=np.float64,
    )
    if coarse_scores.size == 0 or float(np.max(coarse_scores)) <= 0.0:
        return 0.0, 1.0

    best_coarse = float(coarse_angles[int(np.argmax(coarse_scores))])
    med_coarse = float(np.median(coarse_scores))

    lo = max(-max_angle_deg, best_coarse - 1.0)
    hi = min(max_angle_deg, best_coarse + 1.0)
    fine_angles = np.arange(lo, hi + 1e-6, 0.1)
    fine_scores = np.array(
        [_projection_sharpness(sig_s, msk_s, float(a)) for a in fine_angles],
        dtype=np.float64,
    )
    best_idx = int(np.argmax(fine_scores))
    best_angle = float(fine_angles[best_idx])
    best_score = float(fine_scores[best_idx])
    gain = best_score / max(med_coarse, 1e-12)
    return best_angle, gain


def _rows_from_rectified_profile(
    sig_roi: np.ndarray, mask_roi: np.ndarray, delta_deg: float, y_offset: int
) -> list[dict[str, Any]]:
    """Summarize horizontal band rows from a rectified projection profile."""
    h, w = sig_roi.shape
    center = (w * 0.5, h * 0.5)
    t = Affine.rotation_about(center, delta_deg, "src", "dst")
    rot_sig = warp(sig_roi, t, w, h, interpolation=cv2.INTER_LINEAR, border_value=0.0)
    rot_msk = warp(mask_roi.astype(np.uint8), t, w, h, interpolation=cv2.INTER_NEAREST, border_value=0)
    counts = np.sum(rot_msk > 0, axis=1).astype(np.float32)
    valid = counts >= max(10.0, 0.30 * w)
    if not np.any(valid):
        return []

    profile = np.zeros(h, dtype=np.float32)
    sums = np.sum(rot_sig * (rot_msk > 0), axis=1)
    profile[valid] = sums[valid] / counts[valid]

    smooth = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 0), 3.0).ravel()
    baseline = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 0), 35.0).ravel()
    response = np.maximum(smooth - baseline, 0.0)
    max_resp = float(np.max(response))
    if max_resp < 0.005:
        return []

    thresh = max(0.005, 0.15 * max_resp)
    peaks = _find_1d_peaks(response, 1, h - 1, thresh, distance=20)
    rows: list[dict[str, Any]] = []
    for p in peaks:
        half_max = baseline[p] + 0.5 * response[p]
        lo = p
        while lo > 0 and smooth[lo] > half_max:
            lo -= 1
        hi = p
        while hi < h - 1 and smooth[hi] > half_max:
            hi += 1
        band_slice = rot_sig[max(0, lo) : min(h, hi + 1), :]
        band_mask = rot_msk[max(0, lo) : min(h, hi + 1), :] > 0
        vals = band_slice[band_mask]
        if vals.size == 0:
            continue
        rows.append(
            {
                "y": float(p + 0.5 + y_offset),
                "height_px": float(max(1, hi - lo)),
                "peak_signal": float(np.max(vals)),
                "mean_signal": float(np.mean(vals)),
                "x_span_px": float(w),
                "component_count": 1,
            }
        )
    return rows


def _find_1d_peaks(
    profile: np.ndarray, start: int, end: int, threshold: float, distance: int
) -> list[int]:
    """Find local maxima in a 1D array separated by at least `distance`."""
    candidates = [
        i
        for i in range(max(1, start), min(len(profile) - 1, end))
        if profile[i] >= threshold and profile[i] > profile[i - 1] and profile[i] >= profile[i + 1]
    ]
    selected: list[int] = []
    for i in sorted(candidates, key=lambda idx: (-float(profile[idx]), idx)):
        if all(abs(i - j) >= distance for j in selected):
            selected.append(i)
    return sorted(selected)


def estimate_horizontal_tilt(
    signal: np.ndarray,
    mask: np.ndarray | None = None,
    max_angle_deg: float = 15.0,
) -> dict[str, Any]:
    """Estimate rotation delta (degrees) to make tilted western blot bands horizontal.

    Follows WBM's ``Affine.rotation_about`` convention: positive degrees rotate
    clockwise visually in image coordinates (y-down). Thus bands sloping
    down-right return a negative ``delta_deg`` to rotate them horizontal.
    """
    sig = np.clip(np.asarray(signal, dtype=np.float32), 0.0, None)
    sig_roi, mask_roi, _, y_offset = _crop_to_mask(sig, mask)
    warnings: list[str] = []

    if sig_roi.size == 0 or sig_roi.shape[0] < 10 or sig_roi.shape[1] < 10:
        return {
            "delta_deg": 0.0,
            "confidence": 0.0,
            "method": "insufficient_signal",
            "band_rows": [],
            "metrics": {
                "sharpness_gain": 1.0,
                "residual_px": 0.0,
                "signal_p99": 0.0,
                "row_count": 0,
            },
            "warnings": ["Region too small for tilt estimation."],
        }

    vals = sig_roi[mask_roi]
    if vals.size < 100:
        return {
            "delta_deg": 0.0,
            "confidence": 0.0,
            "method": "insufficient_signal",
            "band_rows": [],
            "metrics": {
                "sharpness_gain": 1.0,
                "residual_px": 0.0,
                "signal_p99": 0.0,
                "row_count": 0,
            },
            "warnings": ["Insufficient masked pixels for tilt estimation."],
        }

    signal_p99 = float(np.percentile(vals, 99))
    median = float(np.median(vals))
    mad = float(np.median(np.abs(vals - median)))
    sigma_noise = 1.4826 * mad
    contrast = signal_p99 - median

    if contrast < 0.015 or (sigma_noise > 1e-4 and contrast < 3.5 * sigma_noise):
        return {
            "delta_deg": 0.0,
            "confidence": 0.0,
            "method": "insufficient_signal",
            "band_rows": [],
            "metrics": {
                "sharpness_gain": 1.0,
                "residual_px": 0.0,
                "signal_p99": signal_p99,
                "row_count": 0,
            },
            "warnings": ["Signal contrast too low for reliable tilt estimation."],
        }

    # Adaptive threshold above background & noise
    thresh = min(
        median + 0.25,
        max(median + 0.02, median + 4.0 * sigma_noise, median + 0.15 * contrast),
    )

    smear_dominated, _, _ = _detect_vertical_smears(sig_roi, mask_roi, thresh)
    if smear_dominated:
        warnings.append("Vertical smear pattern detected; skipping tilt adjustment.")
        return {
            "delta_deg": 0.0,
            "confidence": 0.1,
            "method": "insufficient_signal",
            "band_rows": [],
            "metrics": {
                "sharpness_gain": 1.0,
                "residual_px": 0.0,
                "signal_p99": signal_p99,
                "row_count": 0,
            },
            "warnings": warnings,
        }

    angle_a, residual_px, band_rows = _estimate_centroid_regression(
        sig_roi, mask_roi, thresh, max_angle_deg, y_offset
    )
    angle_b, sharpness_gain = _estimate_projection_sharpness(
        sig_roi, mask_roi, max_angle_deg
    )

    proj_valid = sharpness_gain >= 1.12
    reg_valid = angle_a is not None

    if reg_valid and proj_valid:
        diff = abs(angle_a - angle_b)
        if diff <= 2.0:
            delta_deg = 0.5 * (angle_a + angle_b)
            method = "centroid_regression+projection"
            gain_score = min(1.0, (sharpness_gain - 1.0) / 1.5)
            res_score = max(0.0, 1.0 - residual_px / 5.0)
            confidence = float(np.clip(0.70 + 0.20 * gain_score + 0.10 * res_score, 0.0, 0.99))
        elif sharpness_gain >= 1.35 and (len(band_rows) <= 1 or residual_px > 2.0):
            delta_deg = angle_b
            method = "projection_sharpness"
            confidence = float(np.clip(0.55 + 0.30 * min(1.0, (sharpness_gain - 1.0) / 2.0), 0.0, 0.90))
            warnings.append(
                f"Centroid regression ({angle_a:.2f} deg) and projection sharpness ({angle_b:.2f} deg) disagreed; used projection sharpness."
            )
        else:
            delta_deg = angle_a
            method = "centroid_regression"
            confidence = float(np.clip(0.55 + 0.25 * max(0.0, 1.0 - residual_px / 4.0), 0.0, 0.88))
            warnings.append(
                f"Centroid regression ({angle_a:.2f} deg) and projection sharpness ({angle_b:.2f} deg) disagreed; used centroid regression."
            )
    elif reg_valid:
        delta_deg = angle_a
        method = "centroid_regression"
        res_score = max(0.0, 1.0 - residual_px / 5.0)
        confidence = float(np.clip(0.55 + 0.30 * res_score, 0.0, 0.90))
    elif proj_valid:
        delta_deg = angle_b
        method = "projection_sharpness"
        gain_score = min(1.0, (sharpness_gain - 1.1) / 1.5)
        confidence = float(np.clip(0.50 + 0.40 * gain_score, 0.0, 0.92))
        if not band_rows:
            band_rows = _rows_from_rectified_profile(sig_roi, mask_roi, delta_deg, y_offset)
    else:
        return {
            "delta_deg": 0.0,
            "confidence": 0.0,
            "method": "insufficient_signal",
            "band_rows": band_rows,
            "metrics": {
                "sharpness_gain": float(sharpness_gain),
                "residual_px": float(residual_px),
                "signal_p99": signal_p99,
                "row_count": len(band_rows),
            },
            "warnings": warnings + ["No coherent horizontal band structure detected."],
        }

    return {
        "delta_deg": float(delta_deg),
        "confidence": float(confidence),
        "method": method,
        "band_rows": band_rows,
        "metrics": {
            "sharpness_gain": float(sharpness_gain),
            "residual_px": float(residual_px),
            "signal_p99": signal_p99,
            "row_count": len(band_rows),
        },
        "warnings": warnings,
    }


def inspect_rectified_bands(
    signal: np.ndarray,
    saturation_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Analyze an already-rectified panel signal image and return numerical metrics."""
    sig = np.clip(np.asarray(signal, dtype=np.float32), 0.0, None)
    h, w = sig.shape[:2]

    if saturation_mask is not None:
        sat = np.asarray(saturation_mask, dtype=bool)
        saturation_fraction = float(np.mean(sat)) if sat.size else 0.0
    else:
        saturation_fraction = 0.0

    signal_p99 = float(np.percentile(sig, 99)) if sig.size else 0.0
    tilt_info = estimate_horizontal_tilt(sig)

    profile = sig.mean(axis=1)
    smooth = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 0), 3.0).ravel()
    baseline = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 0), 35.0).ravel()
    response = np.maximum(smooth - baseline, 0.0)
    max_resp = float(np.max(response)) if response.size else 0.0

    rows: list[dict[str, Any]] = []
    all_lane_peaks: set[int] = set()

    if max_resp >= 0.005:
        thresh = max(0.005, 0.12 * max_resp)
        peaks = _find_1d_peaks(response, 1, h - 1, thresh, distance=20)
        for p in peaks:
            half_max = baseline[p] + 0.5 * response[p]
            lo = p
            while lo > 0 and smooth[lo] > half_max:
                lo -= 1
            hi = p
            while hi < h - 1 and smooth[hi] > half_max:
                hi += 1
            y0, y1 = max(0, lo), min(h, hi + 1)
            band = sig[y0:y1, :]
            col_profile = cv2.GaussianBlur(
                band.mean(axis=0).reshape(1, -1), (0, 1), 3.0
            ).ravel()
            col_thresh = max(0.005, float(np.percentile(col_profile, 20) + 0.15 * np.ptp(col_profile)))
            lane_peaks = _find_1d_peaks(col_profile, 1, w - 1, col_thresh, distance=20)
            all_lane_peaks.update(lane_peaks)

            rows.append(
                {
                    "y": float(p + 0.5),
                    "height_px": float(max(1, hi - lo)),
                    "peak_signal": float(np.max(band)),
                    "mean_signal": float(np.mean(band)),
                    "prominence": float(response[p]),
                    "lane_peaks_x": lane_peaks,
                }
            )

    return {
        "rows": rows,
        "row_positions_y": [r["y"] for r in rows],
        "peak_signals": [r["peak_signal"] for r in rows],
        "lane_peaks_x": sorted(all_lane_peaks),
        "saturation_fraction": saturation_fraction,
        "residual_tilt_deg": float(tilt_info["delta_deg"]),
        "tilt_confidence": float(tilt_info["confidence"]),
        "signal_p99": signal_p99,
    }
