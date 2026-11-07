"""Create deterministic, unconfirmed format-3 proposals in image-edge coordinates.

One membrane supplies one figure row. All detected rows remain switchable candidates.
Initial selection maximizes 2 * lane_structure + 2 * signal_strength + 3 * prominence
- vertical_continuity + 2 * contains_strongest_component. Lane structure rewards
repeated discrete column-profile peaks with regular spacing; signal strength is the band's 90th-percentile background-
subtracted darkness. Vertical continuity penalizes smears. These heuristic scores
rank candidates, do not estimate confidence, and do not identify an antibody.
The strongest-component bonus favors a crop containing the largest integrated
interior band signal, even if another faint row has more regular lane spacing.
Ties use stable top-to-bottom candidate ids. Each crop independently selects the
longest registered exposure without red display pixels, or the fewest-red fallback.
"""
import math

import cv2
import numpy as np

from wb.geometry import Affine, Rect, crop_polygon_in_source, polygon_within_frame, warp
from wb.rendering import frame_for_capture, load_source_pixels


def _red(image):
    return (image[..., 0] > 240) & (image[..., 1] < 20) & (image[..., 2] < 20)


def _gray(image):
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    gray[_red(image)] = 255
    return cv2.GaussianBlur(255 - gray, (0, 0), 3).astype(np.float32) / 255


def _register(template, moving, mask, source, target, diagnostics=None):
    # ECC's matrix maps template coordinates into moving coordinates. Invert it.
    scale = .5
    size = (round(template.shape[1] * scale), round(template.shape[0] * scale))
    a = cv2.resize(_gray(template), size, interpolation=cv2.INTER_AREA)
    b = cv2.resize(_gray(moving), size, interpolation=cv2.INTER_AREA)
    valid = mask & ~_red(template) & ~_red(moving)
    valid = cv2.erode(valid.astype(np.uint8), np.ones((9, 9), np.uint8))
    valid = cv2.resize(valid, size, interpolation=cv2.INTER_NEAREST)
    mat = np.eye(2, 3, dtype=np.float32)
    try:
        score, mat = cv2.findTransformECC(a, b, mat, cv2.MOTION_TRANSLATION,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), valid, 5)
    except cv2.error:
        if diagnostics is not None:
            diagnostics.append('ECC did not converge')
        return None
    dx, dy = -float(mat[0, 2]) / scale, -float(mat[1, 2]) / scale
    if diagnostics is not None:
        diagnostics.append(f'ECC={score:.4f}, translation=({dx:.1f}, {dy:.1f}) px')
    if not np.isfinite([score, dx, dy]).all() or max(abs(dx), abs(dy)) >= 20 or score < .15:
        return None
    return Affine.translation(dx, dy, source, target), float(score)



def _register_outlines(moving, polygons, source, target):
    scale = .5
    height, width = moving.shape[:2]
    size = (round(width * scale), round(height * scale))
    image = cv2.resize(moving, size, interpolation=cv2.INTER_AREA)
    red = cv2.resize(_red(moving).astype(np.uint8), size, interpolation=cv2.INTER_NEAREST)
    red = cv2.dilate(red, np.ones((9, 9), np.uint8))
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    gray[red != 0] = 255
    edges = cv2.Canny(cv2.GaussianBlur(gray, (0, 0), 1), 3, 10)
    edges[red != 0] = 0
    distance = np.minimum(cv2.distanceTransform(255 - edges, cv2.DIST_L2, 3), 6)
    def match(outline):
        y, x = np.where(outline != 0)
        best = None
        for dy in range(-9, 10):
            for dx in range(-9, 10):
                valid = (x + dx >= 0) & (x + dx < size[0]) & (y + dy >= 0) & (y + dy < size[1])
                xx, yy = x[valid] + dx, y[valid] + dy
                visible = red[yy, xx] == 0
                if np.count_nonzero(visible) < len(x) * .5:
                    continue
                mean = float(distance[yy[visible], xx[visible]].mean())
                candidate = (mean, abs(dx) + abs(dy), dx, dy)
                if best is None or candidate < best:
                    best = candidate
        return best

    # A colorimetric membrane need not have visible edges in chemiluminescence.
    # Such outlines can match a band or an unrelated boundary at the search limit
    # and pull the registration away from the membranes with visible edges.
    supported = np.zeros((size[1], size[0]), np.uint8)
    for poly in polygons:
        outline = np.zeros_like(supported)
        cv2.polylines(outline, [np.rint((poly - .5) * scale).astype(np.int32)], True, 1, 1)
        local = match(outline)
        if local is not None and local[0] <= 4 and max(abs(local[2]), abs(local[3])) < 9:
            supported |= outline
    best = match(supported) if np.any(supported) else None
    if best is None or best[0] > 4 or max(abs(best[2]), abs(best[3])) >= 9:
        return None
    _, _, dx, dy = best
    return Affine.translation(-dx / scale, -dy / scale, source, target), 1 - best[0] / 6


def _record(kind, transform, score, outline=False):
    return {'kind': kind, 'from_frame_id': transform.from_frame_id,
            'to_frame_id': transform.to_frame_id, 'matrix': transform.matrix.tolist(),
            'status': 'candidate',
            'method': 'masked supported membrane-outline distance' if outline else 'masked blurred grayscale translation ECC',
            'score': {'outline_score' if outline else 'ecc_correlation': score}}


def _peaks(profile, start, end, threshold, distance):
    candidates = [i for i in range(max(1, start), min(len(profile)-1, end))
                  if profile[i] >= threshold and profile[i] > profile[i-1] and profile[i] >= profile[i+1]]
    selected = []
    for i in sorted(candidates, key=lambda n: (-profile[n], n)):
        if all(abs(i-j) >= distance for j in selected):
            selected.append(i)
    return sorted(selected)


def _fit_quad(contour, epsilon):
    hull = cv2.convexHull(contour)
    approx = cv2.approxPolyDP(contour, epsilon * cv2.arcLength(contour, True), True).reshape(-1, 2)
    area = cv2.contourArea(contour)
    tapered = False
    if len(approx) == 4:
        lengths = np.linalg.norm(approx - np.roll(approx, -1, axis=0), axis=1)
        pair = [0, 2] if lengths[0] + lengths[2] > lengths[1] + lengths[3] else [1, 3]
        tapered = max(lengths[pair]) > min(lengths[pair]) * 1.05
    if (len(approx) != 4 or not cv2.isContourConvex(approx)
            or cv2.contourArea(approx) < area * .94 or tapered):
        box = cv2.minAreaRect(hull)
        x, y, w, h = cv2.boundingRect(hull)
        # Near-axis hulls with uneven illumination do not justify a skewed enclosing box.
        if w * h < box[1][0] * box[1][1] * 1.15:
            approx = np.array([[x, y], [x + w - 1, y], [x + w - 1, y + h - 1], [x, y + h - 1]])
        else:
            approx = cv2.boxPoints(box)
    poly = approx.astype(float) + .5
    poly = poly[np.argsort(poly[:, 1])]
    return np.array([*sorted(poly[:2], key=lambda p: p[0]), *sorted(poly[2:], key=lambda p: -p[0])])


def _components(image):
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(cv2.GaussianBlur(gray, (21, 21), 0), 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = gray.shape
    polygons, warnings = [], []
    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, w, h = cv2.boundingRect(contour)
        rect_area = w * h
        approx = cv2.approxPolyDP(contour, .02 * cv2.arcLength(contour, True), True).reshape(-1, 2)
        reasons = []
        if area < width * height * .002 or w < 40 or h < 40:
            reasons.append('too small')
        if x == 0 or y == 0 or x + w >= width or y + h >= height:
            reasons.append('touches image border')
        if area / max(rect_area, 1) < .65:
            reasons.append('low bounding-box fill')
        box = cv2.minAreaRect(contour)[1]
        if area / max(box[0] * box[1], 1) < .8:
            reasons.append('low rectangularity')
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            reasons.append('not a convex quadrilateral')
        if reasons:
            warnings.append(f'Rejected bright component at ({x}, {y}), area {area:.0f}: {", ".join(reasons)}.')
            continue
        polygons.append(_fit_quad(contour, .02))
    for candidate in _bright_components(gray):
        center = tuple(map(float, candidate.mean(axis=0)))
        if not any(cv2.pointPolygonTest(p.astype(np.float32), center, False) >= 0 for p in polygons):
            polygons.append(candidate)
    polygons.sort(key=lambda p: (float(p[:, 1].mean()), float(p[:, 0].mean())))
    return polygons, warnings



def _bright_components(gray):
    blurred = cv2.GaussianBlur(gray, (21, 21), 0)
    height, width = gray.shape
    candidates = []
    for threshold in np.linspace(np.percentile(blurred, 45), np.percentile(blurred, 75), 37):
        binary = (blurred > threshold).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)
            if (area < width * height * .02 or x == 0 or y == 0 or
                    x + w >= width or y + h >= height):
                continue
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            box = cv2.minAreaRect(hull)[1]
            if area / hull_area < .72 or hull_area / max(box[0] * box[1], 1) < .82:
                continue
            approx = cv2.approxPolyDP(hull, .025 * cv2.arcLength(hull, True), True)
            if len(approx) == 4:
                candidates.append((area, _fit_quad(hull, .025)))
    polygons = []
    for _, poly in sorted(candidates, key=lambda item: -item[0]):
        center = tuple(map(float, poly.mean(axis=0)))
        if any(cv2.pointPolygonTest(p.astype(np.float32), center, False) >= 0 for p in polygons):
            continue
        poly = poly[np.argsort(poly[:, 1])]
        polygons.append(np.array([*sorted(poly[:2], key=lambda p: p[0]),
                                  *sorted(poly[2:], key=lambda p: -p[0])]))
    return polygons


def _masks(shape, polygons):
    mask = np.zeros(shape, np.uint8)
    regmask = np.zeros(shape, np.uint8)
    for poly in polygons:
        component = np.zeros(shape, np.uint8)
        cv2.fillPoly(component, [np.round(poly - .5).astype(np.int32)], 1)
        mask |= component
        x0, _ = poly.min(axis=0)
        x1, _ = poly.max(axis=0)
        component[:, :int(x0 + .12 * (x1 - x0))] = 0
        component[:, int(x1 - .06 * (x1 - x0)):] = 0
        regmask |= component
    return mask.astype(bool), regmask.astype(bool)


def _signal(image):
    darkness = (255 - cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)).astype(np.float32) / 255
    darkness[_red(image)] = 1
    return darkness


def _composite(registered, sample_ids, image, transform, width, height):
    signal = np.zeros((height, width), np.float32)
    for capture, _, alignment in registered:
        if capture.capture_id not in sample_ids:
            continue
        pixels = _signal(image(capture))
        smooth = cv2.GaussianBlur(pixels, (0, 0), 1.5)
        residual = np.maximum(smooth - cv2.GaussianBlur(smooth, (0, 0), 30), 0)
        aligned = warp(residual, transform @ alignment, width, height)
        signal = np.maximum(signal, aligned)
    return signal


def _band_blobs(signal, polygon, threshold=.18):
    mask, _ = _masks(signal.shape, [polygon])
    mask = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8))
    binary = ((signal > threshold) & (mask != 0)).astype(np.uint8)
    n, labels, stats, centers = cv2.connectedComponentsWithStats(binary, 8)
    blobs = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 30:
            continue
        x, y, w, h = stats[i, :4]
        yy, xx = np.nonzero(labels[y:y + h, x:x + w] == i)
        points = np.column_stack((xx + x + .5, yy + y + .5))
        blobs.append((stats[i], centers[i] + .5, points))
    return blobs


def _orientation(refpoly, refimage, signal, label, warnings):
    vertical = np.ptp(refpoly[:, 1]) > 2 * np.ptp(refpoly[:, 0])
    edges = [refpoly[1] - refpoly[0], refpoly[2] - refpoly[3]]
    edge_angle = float(np.mean([math.degrees(math.atan2(v[1], v[0])) for v in edges]))
    if vertical:
        angles = []
        for _, _, points in _band_blobs(signal, refpoly):
            covariance = np.cov(points.T)
            values, vectors = np.linalg.eigh(covariance)
            if values[1] > values[0] * 2:
                dx, dy = vectors[:, 1]
                angle = math.degrees(math.atan2(dy, dx)) % 180
                # A narrow, vertically cut membrane can still contain horizontal
                # sample bands. Its aspect ratio does not establish band direction.
                angles.append(angle if angle <= 135 else angle - 180)
        direction = float(np.median(angles)) if angles else edge_angle
        warnings.append(f'{label}: band direction is ambiguous by 180 degrees; verify the orientation. '
                        'Membrane aspect ratio does not establish sample direction.')
        return -direction, '2D band component major axes' if angles else 'membrane edge fallback', {
            'band_direction_degrees': direction if angles else None, 'elongated_component_count': len(angles),
            'orientation_ambiguity_degrees': 180}
    # Centroid slopes remain reliable when band widths differ across lanes.
    _, regmask = _masks(refimage.shape[:2], [refpoly])
    binary = ((_gray(refimage) > .28) & regmask).astype(np.uint8)
    n, _, stats, centers = cv2.connectedComponentsWithStats(binary, 8)
    points = [centers[i] + .5 for i in range(1, n)
              if stats[i, cv2.CC_STAT_AREA] >= 30 and stats[i, cv2.CC_STAT_WIDTH] >= 10]
    groups = []
    for point in sorted(points, key=lambda p: p[1]):
        if not groups or point[1] - groups[-1][-1][1] > 35:
            groups.append([])
        groups[-1].append(point)
    slopes = []
    for points in groups:
        points = np.asarray(points)
        if len(points) >= 3 and np.ptp(points[:, 0]) > 200:
            slope = float(np.polyfit(points[:, 0], points[:, 1], 1)[0])
            if abs(slope) < .1:
                slopes.append(slope)
    direction = math.degrees(math.atan(float(np.median(slopes)))) if slopes else edge_angle
    return -direction, 'band blob row slopes' if slopes else 'membrane edge fallback', {
        'band_direction_degrees': direction if slopes else None, 'membrane_edge_degrees': edge_angle,
        'fitted_row_count': len(slopes)}


def _vertical_bounds(polygon, left, right):
    bounds = []
    for x in (left, right):
        intersections = []
        for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
            if min(a[0], b[0]) <= x <= max(a[0], b[0]) and abs(b[0] - a[0]) > 1e-8:
                intersections.append(a[1] + (x - a[0]) * (b[1] - a[1]) / (b[0] - a[0]))
        if len(intersections) < 2:
            return None
        bounds.append((min(intersections), max(intersections)))
    return max(v[0] for v in bounds) + 1, min(v[1] for v in bounds) - 1


def _row_peaks(profile, start, end, spacing=40):
    smooth = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 0), 5).ravel()
    baseline = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 0), 35).ravel()
    response = smooth - baseline
    threshold = max(.0015, float(response[start:end].max()) * .035)
    peaks = _peaks(response, start, end, threshold, spacing)
    result = []
    for peak in peaks:
        lo, hi = max(start, peak - 80), min(end, peak + 81)
        prominence = float(smooth[peak] - max(smooth[lo:peak + 1].min(), smooth[peak:hi].min()))
        if prominence >= threshold:
            result.append((peak + .5, prominence))
    return result, smooth


def _exposure(rect, d, registered, image):
    counts = {}
    for capture, frame, transform in registered:
        polygon = crop_polygon_in_source(transform, d, rect)
        raster = np.zeros((frame.height, frame.width), np.uint8)
        cv2.fillPoly(raster, [np.rint(polygon - .5).astype(np.int32)], 1)
        counts[capture.capture_id] = int(np.count_nonzero(_red(image(capture)) & (raster != 0)))
    free = [c for c, _, _ in registered if counts[c.capture_id] == 0]
    if free:
        chosen = max(free, key=lambda c: (c.exposure_s or 0, c.capture_id))
        reason = 'longest exposure with no red display pixels inside this crop in its source frame'
    else:
        chosen = min((c for c, _, _ in registered),
                     key=lambda c: (counts[c.capture_id], -(c.exposure_s or 0), c.capture_id))
        reason = 'fallback: all registered exposures contain red display pixels; selected the fewest, then longest'
    return chosen.capture_id, reason, counts


def _row_score(signal, rect, y, prominence):
    x0, x1 = math.ceil(rect.x0), math.floor(rect.x1)
    y0, y1 = max(0, math.floor(rect.y0)), math.ceil(rect.y1)
    core = signal[max(y0, int(y) - 12):min(y1, int(y) + 13), x0:x1]
    column = cv2.GaussianBlur(core.mean(axis=0).reshape(1, -1), (0, 1), 3).ravel()
    threshold = max(.005, float(np.percentile(column, 20) + .1 * np.ptp(column)))
    peaks = _peaks(column, 1, len(column) - 1, threshold, 25)
    if len(peaks) >= 3:
        gaps = np.diff(peaks)
        regularity = float(1 / (1 + np.std(gaps) / max(np.mean(gaps), 1)))
    else:
        regularity = 0.
    lane_structure = min(len(peaks) / 5, 1) * regularity
    strength = float(np.percentile(core, 90))
    local = signal[y0:y1, x0:x1]
    active = local > max(.035, strength * .3)
    vertical_continuity = float(np.mean(active.mean(axis=0)))
    score = 2 * lane_structure + 2 * strength + 3 * prominence - vertical_continuity
    return float(score), {'lane_peak_count': len(peaks), 'lane_regularity': regularity,
                          'lane_structure': lane_structure, 'strength_p90': strength,
                          'profile_prominence': prominence, 'vertical_continuity': vertical_continuity}


def _row_candidates(signal, rectpoly, left, right, d, ref, registered, image, label, warnings):
    # A tall cut membrane with horizontal bands needs a row-local width. The
    # intersection over the full membrane height would trim its outer lanes.
    local_width = np.ptp(rectpoly[:, 1]) > np.ptp(rectpoly[:, 0])
    bounds = (float(rectpoly[:, 1].min()) + 1, float(rectpoly[:, 1].max()) - 1) if local_width else _vertical_bounds(rectpoly, left, right)
    if bounds is None or bounds[1] - bounds[0] < 4:
        warnings.append(f'{label}: no common sample rectangle fits inside the membrane.')
        return []
    # Search the polygon's full height: an edge band may be below the bottom
    # of the narrower rectangle that fits across the entire membrane width.
    start = max(1, math.ceil(float(rectpoly[:, 1].min()) + 1))
    end = min(signal.shape[0] - 1, math.floor(float(rectpoly[:, 1].max()) - 1))
    profile_left, profile_right = (rectpoly[:, 0].min(), rectpoly[:, 0].max()) if local_width else (left, right)
    profile = signal[:, max(0, math.ceil(profile_left)):math.floor(profile_right)].mean(axis=1)
    peaks, smooth = _row_peaks(profile, start, end)
    blobs = _band_blobs(signal, rectpoly, threshold=.22)
    for _, point, _ in sorted(blobs, key=lambda b: b[1][1]):
        x, y = point
        if left <= x <= right and start <= y < end and all(abs(y - p[0]) >= 40 for p in peaks):
            peaks.append((float(y), 0.))
    peaks.sort()
    rows = []
    for i, (y, prominence) in enumerate(peaks):
        previous = peaks[i - 1][0] if i else start
        following = peaks[i + 1][0] if i + 1 < len(peaks) else end
        lo, hi = max(start, (previous + y) / 2), min(end, (y + following) / 2)
        span0, span1 = int(y), int(y) + 1
        threshold = max(float(smooth[int(y)]) * .25, .002)
        while span0 > lo and smooth[span0 - 1] >= threshold:
            span0 -= 1
        while span1 < hi and smooth[span1] >= threshold:
            span1 += 1
        associated = [b for b in blobs if lo <= b[1][1] <= hi and left <= b[1][0] <= right]
        if associated:
            span0 = min(span0, min(float(np.percentile(b[2][:, 1], 5)) for b in associated))
            span1 = max(span1, max(float(np.percentile(b[2][:, 1], 95)) for b in associated))
        margin = min(45, max(12, min(y - previous, following - y) / 2))
        desired0, desired1 = span0 - margin, span1 + margin
        y0, y1 = max(bounds[0], desired0), min(bounds[1], desired1)
        row_left, row_right = left, right
        if local_width:
            horizontal = _vertical_bounds(rectpoly[:, ::-1], y0, y1)
            if horizontal is None or horizontal[1] <= horizontal[0]:
                continue
            row_left, row_right = horizontal
        edge_expanded = False
        if associated and (span0 < y0 or span1 > y1):
            # The outline is an estimate. Do not cut observed band support just
            # to keep all four corners in the inscribed membrane rectangle.
            expanded = Rect(row_left, min(y0, span0 - 12), row_right, max(y1, span1 + 12))
            if polygon_within_frame(d.inverse().apply(expanded.corners()), ref.width, ref.height):
                y0, y1 = expanded.y0, expanded.y1
                edge_expanded = True
                warnings.append(f'{label}: band row at {y:.1f} extends past the estimated membrane crop; '
                                'preserved the detected band span with a 12 px margin. Verify the membrane boundary.')
        if y1 <= y0:
            continue
        rect = Rect(row_left, y0, row_right, y1)
        if not polygon_within_frame(d.inverse().apply(rect.corners()), ref.width, ref.height):
            warnings.append(f'{label}: band row at {y:.1f} extends beyond the reference frame and was omitted.')
            continue
        if y0 > desired0 or y1 < desired1:
            warnings.append(f'{label}: band row at {y:.1f} crop was shrunk to fit the membrane edge.')
        exposure_id, reason, counts = _exposure(rect, d, registered, image)
        score, components = _row_score(signal, rect, y, prominence)
        rows.append({'candidate_id': f'{label}_R{len(rows) + 1}',
                     'crop_rect_rectified': dict(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1),
                     'band_row_y_rectified': float(y), 'selected_exposure_id': exposure_id,
                     'exposure_reason': reason, 'red_pixels_in_crop_by_capture': counts, 'score': score,
                     'evidence': {'score_components': components,
                                  'expanded_past_estimated_membrane_edge': edge_expanded,
                                  'band_span_y_rectified': [float(span0), float(span1)],
                                  'signal': 'maximum of denoised, background-subtracted short/mid/long exposures; '
                                            'red display pixels count as maximum signal'}})
    if not rows:
        warnings.append(f'{label}: no band rows detected; inspect images and draw a row manually.')
    return sorted(rows, key=lambda row: (-row['score'], row['candidate_id']))


def _component_support(signal, rectpoly, rows, d, registered, image):
    if not rows:
        return
    sample = Rect(**rows[0]['crop_rect_rectified'])
    local = signal[:, math.ceil(sample.x0):math.floor(sample.x1)]
    threshold = max(.015, float(np.percentile(local[local > 0], 98)) * .4) if np.any(local > 0) else .015
    blobs = _band_blobs(signal, rectpoly, threshold)
    blobs = [b for b in blobs if sample.x0 <= b[1][0] <= sample.x1
             and cv2.pointPolygonTest(rectpoly.astype(np.float32), tuple(map(float, b[1])), True) >= 15]
    strongest = max(blobs, key=lambda b: float(signal[(b[2][:, 1] - .5).astype(int),
                                                    (b[2][:, 0] - .5).astype(int)].sum()), default=None)
    red_centers = []
    for capture, _, transform in registered:
        red = _red(image(capture)).astype(np.uint8)
        n, _, stats, centers = cv2.connectedComponentsWithStats(red, 8)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= 30:
                point = (d @ transform).apply(centers[i] + .5)
                if cv2.pointPolygonTest(rectpoly.astype(np.float32), tuple(map(float, point)), False) >= 0:
                    red_centers.append(point)
    for row in rows:
        crop = Rect(**row['crop_rect_rectified'])

        def inside(point):
            return bool(crop.x0 <= point[0] <= crop.x1 and crop.y0 <= point[1] <= crop.y1)

        red_inside = sum(inside(point) for point in red_centers)
        strong_inside = strongest is not None and inside(strongest[1])
        bonus = 2. if strong_inside else 0.
        row['score'] += bonus
        row['evidence']['score_components']['strongest_component_bonus'] = bonus
        row['evidence']['band_support'] = {
            'red_component_count': red_inside,
            'contains_strongest_component': strong_inside,
            'strongest_component_center_rectified': strongest[1].tolist() if strongest is not None else None,
            'strongest_component_rule': 'largest integrated signal among sample-area blobs, centers >= 15 px '
                                        'from membrane edge; threshold = max(0.015, 0.4 * positive signal p98), '
                                        'area >= 30 px',
            'selection_basis': 'contains red-display band component' if red_inside else (
                'contains strongest signal component' if strong_inside else 'profile and lane-structure score')}


def detect_marker_lanes(marker_image, transform, width, height, rectpoly, *, fallback=False):
    marker_rect = warp(marker_image, transform, width, height, border_value=(255, 255, 255))
    marker_signal = _gray(marker_rect)
    left = float(rectpoly[:, 0].min())
    right = float(rectpoly[:, 0].max())
    span_w = right - left
    if span_w < 8:
        return None
    poly_y0, poly_y1 = float(rectpoly[:, 1].min()), float(rectpoly[:, 1].max())
    inset_y = min(20.0, max(2.0, (poly_y1 - poly_y0) * 0.07))
    y0 = max(0, int(poly_y0 + inset_y))
    y1 = min(height, int(poly_y1 - inset_y))
    mh, mw = marker_image.shape[:2]

    def fit_in_frame(x0, x1):
        for shrink in (0.0, 1.0, 2.0, 4.0):
            xx0, xx1 = x0 + shrink, x1 - shrink
            if xx1 - xx0 < 3 or poly_y1 - poly_y0 <= 2 * shrink:
                break
            box = Rect(xx0, poly_y0 + shrink, xx1, poly_y1 - shrink)
            if polygon_within_frame(transform.inverse().apply(box.corners()), mw, mh):
                return xx0, xx1
        return None

    candidates = [
        ('left', left, min(right, left + max(5.0, 0.115 * span_w))),
        ('right', max(left, right - max(5.0, 0.145 * span_w)), right),
    ]
    scored = []
    for side, raw_x0, raw_x1 in candidates:
        fitted = fit_in_frame(raw_x0, raw_x1)
        if fitted is None:
            continue
        sx0, sx1 = fitted
        ladder = marker_signal[y0:y1, max(0, int(sx0)):min(width, int(math.ceil(sx1)))]
        if ladder.shape[0] < 6 or ladder.shape[1] < 2:
            continue
        mean_prof = ladder.mean(axis=1).reshape(-1, 1)
        mean_resp = (cv2.GaussianBlur(mean_prof, (1, 9), 0) - cv2.GaussianBlur(mean_prof, (1, 51), 0)).ravel()
        peaks_mean = _peaks(mean_resp, 0, len(mean_resp), .006, 12)
        max_prof = ladder.max(axis=1).reshape(-1, 1)
        k_hi = min(45, max(15, (len(max_prof) // 3) | 1))
        max_resp = (cv2.GaussianBlur(max_prof, (1, 7), 0) - cv2.GaussianBlur(max_prof, (1, k_hi), 0)).ravel()
        peaks_max = _peaks(max_resp, 0, len(max_resp), .02, 12)
        contrast = float(np.percentile(ladder, 99) - np.percentile(ladder, 30))
        valid = len(peaks_mean) >= 5 or (len(peaks_max) >= 3 and contrast >= 0.30)
        scored.append((valid, len(peaks_mean) >= 5 and side == 'left', max(len(peaks_mean), len(peaks_max)), contrast, side, sx0, sx1))

    strong = [item for item in scored if item[0]]
    if strong:
        _, _, _, _, side, sx0, sx1 = max(strong, key=lambda item: (item[1], item[2], round(item[3], 2), item[4] == 'left'))
        return {'x0_rectified': sx0, 'x1_rectified': sx1,
                'evidence': f'Dark marks in the {side} margin of the colorimetric membrane; verify manually.'}
    if fallback and scored:
        _, _, _, _, side, sx0, sx1 = max(scored, key=lambda item: (round(item[3], 3), item[2], item[4] == 'left'))
        return {'x0_rectified': sx0, 'x1_rectified': sx1,
                'evidence': f'Colorimetric {side} margin extracted on membrane confirmation.'}
    return None


def find_ladder_columns(marker_image, transform, width, height, rectpoly):
    """Ladder lanes anywhere across a membrane, in rectified x.

    A ladder lane is a column of short horizontal marks: strong vertical
    gradients and weak horizontal ones. Membrane edges are vertical lines, so
    they score negative; handwriting has both gradients and largely cancels.
    Pixels outside the membrane polygon are flattened first so the tray and
    its droplets cannot score.
    """
    marker_rect = warp(marker_image, transform, width, height, border_value=(255, 255, 255))
    dark = _gray(marker_rect)
    poly = np.asarray(rectpoly, dtype=np.float64)
    left, right = float(poly[:, 0].min()), float(poly[:, 0].max())
    top, bottom = float(poly[:, 1].min()), float(poly[:, 1].max())
    if right - left < 30 or bottom - top < 30:
        return []
    mask = np.zeros(dark.shape, np.uint8)
    cv2.fillPoly(mask, [np.round(poly - .5).astype(np.int32)], 1)
    mask = cv2.erode(mask, np.ones((15, 15), np.uint8)).astype(bool)
    if mask.sum() < 100:
        return []
    dark = np.where(mask, dark, float(np.median(dark[mask])))
    inset = min(20.0, max(2.0, (bottom - top) * .05))
    y0, y1 = max(0, int(top + inset)), min(dark.shape[0], int(bottom - inset))
    x0, x1 = max(0, int(left)), min(dark.shape[1], int(math.ceil(right)))
    roi = dark[y0:y1, x0:x1]
    gy = np.abs(np.diff(roi, axis=0))[:, :-1]
    gx = np.abs(np.diff(roi, axis=1))[:-1, :]
    score = (gy - gx).mean(axis=0)
    score = cv2.GaussianBlur(score.reshape(1, -1).astype(np.float32), (0, 0), 6).ravel()
    if score.size < 5 or score.max() <= 0:
        return []
    columns = []
    for peak in _peaks(score, 0, len(score), .35 * float(score.max()), 25):
        half = .45 * score[peak]
        a = peak
        while a > 0 and score[a - 1] > half:
            a -= 1
        b = peak
        while b < len(score) - 1 and score[b + 1] > half:
            b += 1
        width_px = b - a + 1
        if width_px < 8 or width_px > .4 * (x1 - x0):
            continue
        strip = roi[:, a:b + 1].mean(axis=1).reshape(-1, 1)
        resp = (cv2.GaussianBlur(strip, (1, 9), 0) - cv2.GaussianBlur(strip, (1, 51), 0)).ravel()
        marks = _peaks(resp, 0, len(resp), .006, 12)
        if len(marks) < 3:
            continue
        columns.append({
            'x0_rectified': float(x0 + a), 'x1_rectified': float(x0 + b + 1),
            'center': float(x0 + peak), 'score': float(score[peak]), 'marks': len(marks),
        })
    return columns


def choose_ladder(marker_image, transform, width, height, rectpoly, prefer_x=None):
    """The ladder lane nearest the samples (or the strongest), fitted to the frame."""
    columns = find_ladder_columns(marker_image, transform, width, height, rectpoly)
    if not columns:
        return None
    if prefer_x is None:
        columns.sort(key=lambda c: -c['score'])
    else:
        strongest = max(c['score'] for c in columns)
        columns = [c for c in columns if c['score'] >= .5 * strongest]
        columns.sort(key=lambda c: abs(c['center'] - prefer_x))
    poly = np.asarray(rectpoly, dtype=np.float64)
    py0, py1 = float(poly[:, 1].min()), float(poly[:, 1].max())
    mh, mw = marker_image.shape[:2]
    for column in columns:
        pad = .15 * (column['x1_rectified'] - column['x0_rectified'])
        for shrink in (0.0, 1.0, 2.0, 4.0):
            box = Rect(column['x0_rectified'] - pad, py0 + shrink, column['x1_rectified'] + pad, py1 - shrink)
            if polygon_within_frame(transform.inverse().apply(box.corners()), mw, mh):
                return {'x0_rectified': box.x0, 'x1_rectified': box.x1,
                        'evidence': f"Ladder lane at x={column['center']:.0f} ({column['marks']} marks) "
                                    'chosen nearest the sample lanes; verify manually.'}
    return None


def _marker_lanes(marker_image, transform, width, height, rectpoly, label, warnings, prefer_x=None):
    detected = choose_ladder(marker_image, transform, width, height, rectpoly, prefer_x)
    if detected is not None:
        return detected
    detected = detect_marker_lanes(marker_image, transform, width, height, rectpoly, fallback=False)
    if detected is not None:
        return detected
    warnings.append(f'{label}: no visible ladder detected; marker lanes left unset.')
    return None


def _membrane(poly, label, rect_id, t_marker, ref, refimage, marker_image, registered, image, warnings, sample_ids):
    refpoly = t_marker.apply(poly)
    center = refpoly.mean(axis=0)
    raw = _composite(registered, sample_ids, image, Affine.identity(ref.frame_id), ref.width, ref.height)
    degrees, method, evidence = _orientation(refpoly, refimage, raw, label, warnings)
    d = Affine.rotation_about(center, degrees, ref.frame_id, rect_id)
    rectpoly = d.apply(refpoly)
    # Rectified coordinates may be negative, particularly for a vertical membrane
    # near the image edge. Shift only the detection raster, then restore the
    # project's coordinates; final rendering still uses the original transform.
    offset = np.maximum(0, -np.floor(rectpoly.min(axis=0)))
    analysis_d = Affine.translation(*offset, rect_id, 'ANALYSIS_' + rect_id) @ d
    rectpoly = analysis_d.apply(refpoly)
    xs = np.sort(rectpoly[:, 0])
    inset = 1.
    left, right = float(xs[1] + inset), float(xs[2] - inset)
    width = max(ref.width, math.ceil(rectpoly[:, 0].max()) + 1)
    height = max(ref.height, math.ceil(rectpoly[:, 1].max()) + 1)
    signal = _composite(registered, sample_ids, image, analysis_d, width, height)
    mask, _ = _masks(signal.shape, [rectpoly])
    signal[~mask] = 0
    rows = _row_candidates(signal, rectpoly, left, right, analysis_d, ref, registered, image, label, warnings)
    _component_support(signal, rectpoly, rows, analysis_d, registered, image)
    rows.sort(key=lambda row: (-row['score'], row['band_row_y_rectified']))
    if rows:
        left = min(row['crop_rect_rectified']['x0'] for row in rows)
        right = max(row['crop_rect_rectified']['x1'] for row in rows)
    if rows and not rows[0]['evidence']['band_support']['red_component_count']:
        warnings.append(f'{label}: selected the strongest interior signal without a red component; '
                        'verify the target row, including faint or nonspecific alternatives.')
    col = signal[max(0, int(rectpoly[:, 1].min())):int(rectpoly[:, 1].max()), :].mean(axis=0)
    col = cv2.GaussianBlur(col.reshape(1, -1), (21, 1), 0).ravel()
    lane_x = _peaks(col, int(left), int(right), float(np.percentile(col[int(left):int(right)], 65)), 25)
    prefer_x = float(np.mean(lane_x)) if lane_x else None
    marker_lanes = _marker_lanes(marker_image, analysis_d @ t_marker, width, height, rectpoly, label, warnings, prefer_x)
    for row in rows:
        for key in ('x0', 'x1'):
            row['crop_rect_rectified'][key] -= float(offset[0])
        for key in ('y0', 'y1'):
            row['crop_rect_rectified'][key] -= float(offset[1])
        row['band_row_y_rectified'] -= float(offset[1])
        ev = row['evidence']
        ev['band_span_y_rectified'] = [v - float(offset[1]) for v in ev['band_span_y_rectified']]
        point = ev['band_support']['strongest_component_center_rectified']
        if point is not None:
            ev['band_support']['strongest_component_center_rectified'] = (np.asarray(point) - offset).tolist()
    if marker_lanes:
        for key in ('x0_rectified', 'x1_rectified'):
            marker_lanes[key] -= float(offset[0])
    left -= float(offset[0])
    right -= float(offset[0])
    return {'label': label, 'panel_label': '', 'polygon_marker_frame': poly.tolist(),
            'polygon_reference_frame': refpoly.tolist(),
            'rotation': {'degrees': degrees, 'center': center.tolist(), 'method': method, 'evidence': evidence},
            'marker_lanes': marker_lanes, 'sample_area': {'x0_rectified': left, 'x1_rectified': right},
            'lane_candidates': {'centers_x_rectified': [float(x + .5 - offset[0]) for x in lane_x],
                                'method': 'peaks in mean rectified darkness column profile', 'status': 'proposed'},
            'row': rows[0] if rows else None, 'row_candidates': rows,
            'method': 'blurred brightness thresholds and convex polygon approximation',
            'parameters': {'blur_kernel': 21, 'minimum_band_spacing_px': 40,
                           'signal_capture_ids': sorted(sample_ids)},
            'evidence': 'Bright rectangular component in the colorimetric frame.'}


def _row_label(index):
    result = ''
    index += 1
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def _signal_polygons(signal):
    """Provisional signal envelopes, never claims about membrane boundaries."""
    height, width = signal.shape
    frame_poly = Rect(1, 1, width - 1, height - 1).corners()
    blobs = _band_blobs(signal, frame_poly, threshold=.015)
    groups = []
    for blob in sorted(blobs, key=lambda b: b[1][1]):
        if not groups or blob[1][1] - np.median([b[1][1] for b in groups[-1]]) > 35:
            groups.append([])
        groups[-1].append(blob)
    polygons = []
    for group in groups:
        clusters = []
        for blob in sorted(group, key=lambda b: b[1][0]):
            if not clusters or blob[1][0] - clusters[-1][-1][1][0] > width * .18:
                clusters.append([])
            clusters[-1].append(blob)
        for cluster in clusters:
            points = np.concatenate([b[2] for b in cluster])
            x0, y0 = points.min(axis=0)
            x1, y1 = points.max(axis=0)
            if x1 - x0 < max(20, width * .02) or y1 - y0 > height * .2:
                continue
            polygons.append(Rect(max(1, x0 - 25), max(1, y0 - 35),
                                 min(width - 1, x1 + 25), min(height - 1, y1 + 35)).corners())
    polygons.sort(key=lambda p: (float(p[:, 1].mean()), float(p[:, 0].mean())))
    return polygons


def _has_row_signal(signal, row):
    # Positive-band count is evidence about signal, never the number of lanes.
    rect = Rect(**row['crop_rect_rectified'])
    components = _band_blobs(signal, rect.corners(), threshold=.08)
    strong = [b for b in components if b[0][cv2.CC_STAT_WIDTH] >= 10
              and b[0][cv2.CC_STAT_WIDTH] >= 1.2 * b[0][cv2.CC_STAT_HEIGHT]]
    row['evidence']['strong_band_component_count'] = len(strong)
    scores = row['evidence']['score_components']
    return bool(strong) or (scores['lane_peak_count'] >= 3 and scores['strength_p90'] >= .05)


def _propose_without_marker(project, image):
    result = {'proposal_format': 3, 'groups': [], 'excluded_image_sets': [],
              'unmatched_marker_frames': [],
              'warnings': ['Colorimetric写真がありません。発光信号から仮の領域を提案しています。'
                           '膜の境界・抗体・分子量は未確認です。']}
    captures = {c.capture_id: c for c in project.captures}
    index = 0
    for series in project.image_sets:
        exposures = sorted((captures[cid] for cid in series.capture_ids),
                           key=lambda c: (c.exposure_s or 0, c.capture_id))
        if not exposures:
            continue
        refcap = exposures[-1]
        ref = frame_for_capture(project, refcap.capture_id)
        refimage = image(refcap)
        registered = [(refcap, ref, Affine.identity(ref.frame_id))]
        transforms, warnings = [], []
        mask = np.ones(refimage.shape[:2], dtype=bool)
        for capture in exposures[:-1]:
            frame = frame_for_capture(project, capture.capture_id)
            reg = None
            if (frame.width, frame.height) == (ref.width, ref.height):
                reg = _register(refimage, image(capture), mask, frame.frame_id, ref.frame_id)
            if reg is None:
                warnings.append(f'{capture.capture_id}: 位置合わせができないため候補から除外しました。')
                continue
            tr, score = reg
            transforms.append(_record('exposure_to_reference', tr, score))
            registered.append((capture, frame, tr))
        sample_ids = {c.capture_id for c, _, _ in registered}
        raw = _composite(registered, sample_ids, image, Affine.identity(ref.frame_id), ref.width, ref.height)
        membranes = []
        for poly in _signal_polygons(raw):
            label = f'M{len(membranes) + 1}'
            center = poly.mean(axis=0)
            degrees, method, evidence = _orientation(poly, refimage, raw, label, warnings)
            d = Affine.rotation_about(center, degrees, ref.frame_id, f'RECT_{series.image_set_id}_{label}')
            rectpoly = d.apply(poly)
            left, right = float(rectpoly[:, 0].min() + 2), float(rectpoly[:, 0].max() - 2)
            signal = _composite(registered, sample_ids, image, d, ref.width, ref.height)
            rows = _row_candidates(signal, rectpoly, left, right, d, ref, registered, image, label, warnings)
            rows = [row for row in rows if _has_row_signal(signal, row)]
            if not rows:
                warnings.append(f'弱い信号の参考領域を保留しました: '
                                f'x={poly[:, 0].min():.0f}〜{poly[:, 0].max():.0f}, '
                                f'y={poly[:, 1].min():.0f}〜{poly[:, 1].max():.0f}。元画像で確認してください。')
                continue
            _component_support(signal, rectpoly, rows, d, registered, image)
            rows.sort(key=lambda row: (-row['score'], row['band_row_y_rectified']))
            membranes.append({'label': label, 'panel_label': _row_label(index),
                'polygon_reference_frame': poly.tolist(), 'polygon_marker_frame': None,
                'rotation': {'degrees': degrees, 'center': center.tolist(), 'method': method, 'evidence': evidence},
                'marker_lanes': None, 'sample_area': {'x0_rectified': left, 'x1_rectified': right},
                'lane_candidates': {'centers_x_rectified': [], 'status': 'proposed'},
                'row': rows[0], 'row_candidates': rows,
                'method': 'chemiluminescent signal envelope without a colorimetric photo',
                'parameters': {'strong_component_threshold': .08,
                               'repeated_signal_minimum_peaks': 3, 'repeated_signal_minimum_p90': .05},
                'evidence': 'Provisional signal crop only; membrane identity and boundaries require review.'})
            index += 1
        if membranes:
            result['groups'].append({'marker_frame_id': None, 'image_set_ids': [series.image_set_id],
                'reference_frame_id': ref.frame_id, 'membranes': membranes,
                'transforms': transforms, 'warnings': warnings})
        else:
            result['excluded_image_sets'].append({'image_set_id': series.image_set_id,
                'reason': '発光信号から段を検出できませんでした。手動で領域を追加してください。'})
    return result


def propose_layout(project, project_dir):
    """Return a JSON-serializable proposal without changing the project."""
    markers = sorted((c for c in project.captures if c.modality == 'colorimetric'),
                     key=lambda c: c.capture_id)
    pixels = {}
    outline_regs = {}

    def image(capture):
        if capture.capture_id not in pixels:
            pixels[capture.capture_id] = load_source_pixels(
                project, project_dir, capture.presentation_source_asset_id)
        return pixels[capture.capture_id]

    result = {'proposal_format': 3, 'groups': [], 'excluded_image_sets': [],
              'unmatched_marker_frames': [], 'warnings': []}
    if not markers:
        return _propose_without_marker(project, image)
    captures = {c.capture_id: c for c in project.captures}
    contexts = []
    for marker in markers:
        frame = frame_for_capture(project, marker.capture_id)
        marker_image = image(marker)
        polygons, warnings = _components(marker_image)
        mask, regmask = _masks(marker_image.shape[:2], polygons)
        contexts.append({'frame': frame, 'image': marker_image, 'polygons': polygons,
                         'mask': mask, 'regmask': regmask, 'warnings': warnings, 'sets': []})
    for series in project.image_sets:
        exposures = [(captures[cid], frame_for_capture(project, cid)) for cid in series.capture_ids]
        exposures.sort(key=lambda cf: (cf[0].exposure_s or 0, cf[0].capture_id))
        matches = []
        reports = []
        for context in contexts:
            mf = context['frame']
            scores = []
            for capture, frame in exposures:
                if (frame.width, frame.height) != (mf.width, mf.height) or not context['polygons']:
                    continue
                detail = []
                reg = _register(context['image'], image(capture), context['regmask'],
                                frame.frame_id, mf.frame_id, detail)
                if reg is None and len(context['polygons']) >= 3:
                    reg = _register_outlines(image(capture), context['polygons'], frame.frame_id, mf.frame_id)
                    outline_regs[(mf.frame_id, frame.frame_id)] = reg
                reports.append(f'{mf.frame_id}/{capture.capture_id}: {detail[0]}')
                if reg:
                    scores.append(reg[1])
            if scores:
                matches.append((max(scores), context))
        if matches:
            score, context = max(matches, key=lambda item: item[0])
            context['sets'].append((series, exposures, score))
        else:
            detail = '; '.join(reports) if reports else 'No compatible dimensions or membrane candidates.'
            result['excluded_image_sets'].append({'image_set_id': series.image_set_id,
                'reason': 'No exposure registered within 20 pixels and ECC >= 0.15. ' + detail})
    for context in contexts:
        mf, marker_image = context['frame'], context['image']
        if not context['sets']:
            result['unmatched_marker_frames'].append({'frame_id': mf.frame_id,
                                                       'reason': 'No image set registered to this photo.'})
            result['warnings'].extend(f'{mf.frame_id}: {w}' for w in context['warnings'])
            continue
        series, exposures, _ = max(context['sets'], key=lambda item: (len(item[1]), item[0].image_set_id))
        same_size = [(c, f) for c, f in exposures if (f.width, f.height) == (mf.width, mf.height)]
        counts = [(int(np.count_nonzero(_red(image(c)) & context['mask'])), c, f) for c, f in same_size]
        counts.sort(key=lambda v: (v[0], -(v[1].exposure_s or 0), v[1].capture_id))
        marker_reg = None
        marker_outline = False
        for minimum, refcap, ref in counts:
            refimage = image(refcap)
            marker_reg = _register(refimage, marker_image, context['regmask'], mf.frame_id, ref.frame_id)
            if marker_reg is None and len(context['polygons']) >= 3:
                direct = outline_regs.get((mf.frame_id, ref.frame_id))
                if direct:
                    marker_reg = (direct[0].inverse(), direct[1])
                    marker_outline = True
            if marker_reg is not None:
                break
        if marker_reg is None:
            result['unmatched_marker_frames'].append({'frame_id': mf.frame_id,
                                                       'reason': 'Selected reference did not register to marker.'})
            for s, _, _ in context['sets']:
                result['excluded_image_sets'].append({'image_set_id': s.image_set_id,
                                                       'reason': 'Selected reference failed marker registration.'})
            continue
        t_marker, score = marker_reg
        transforms = [_record('marker_to_reference', t_marker, score, marker_outline)]
        registered = [(refcap, ref, Affine.identity(ref.frame_id))]
        warnings = context['warnings'][:]
        for s, group, set_score in context['sets']:
            warnings.append(f'{s.image_set_id} best marker registration score: {set_score:.4f}.')
            for c, f in group:
                outline = False
                if f.frame_id == ref.frame_id:
                    continue
                if (f.width, f.height) != (ref.width, ref.height):
                    reg = None
                else:
                    reg = _register(refimage, image(c), context['regmask'], f.frame_id, ref.frame_id)
                if reg is None and len(context['polygons']) >= 3:
                    direct = outline_regs.get((mf.frame_id, f.frame_id))
                    if direct:
                        composed = t_marker @ direct[0]
                        if max(abs(composed.matrix[0, 2]), abs(composed.matrix[1, 2])) < 20:
                            reg = (composed, direct[1])
                            outline = True
                if reg is None:
                    warnings.append(f'{c.capture_id} did not register to the reference and was excluded.')
                    continue
                t, corr = reg
                transforms.append(_record('exposure_to_reference', t, corr, outline))
                registered.append((c, f, t))
        group_index = len(result['groups']) + 1
        sample_ids = set()
        for _, group, _ in context['sets']:
            ids = [c.capture_id for c, _ in group if any(c.capture_id == r[0].capture_id for r in registered)]
            if ids:
                sample_ids.update(ids[i] for i in {0, len(ids) // 2, len(ids) - 1})
        membranes = []
        for index, poly in enumerate(context['polygons'], 1):
            label = f'M{index}'
            rect_id = f'RECT_G{group_index}_{label}' if len(markers) > 1 else f'RECT_{label}'
            membranes.append(_membrane(poly, label, rect_id, t_marker, ref, refimage, marker_image,
                                       registered, image, warnings, sample_ids))
        result['groups'].append({'marker_frame_id': mf.frame_id,
                                 'image_set_ids': [s.image_set_id for s, _, _ in context['sets']],
                                 'reference_frame_id': ref.frame_id, 'membranes': membranes,
                                 'transforms': transforms, 'warnings': warnings})
    index = 0
    for group in result['groups']:
        group['membranes'].sort(key=lambda m: (np.mean(np.array(m['polygon_reference_frame'])[:, 1]),
                                               np.mean(np.array(m['polygon_reference_frame'])[:, 0])))
        for membrane in group['membranes']:
            membrane['panel_label'] = _row_label(index)
            index += 1
    return result
