"""Write a fictional ChemiDoc-style export for the promo video (no real data).

Two membranes on a tray, a prestained ladder on the left of each, five exposures
of chemiluminescence (the longest saturates the strongest bands) and one
colorimetric image. Names, dates and lane contents are made up. The colorimetric
image shows the sample bands faintly so the current marker registration can align it.

    uv run python promo/make_demo_data.py promo/data/ChemiDoc_export.zip
"""

import io
from pathlib import Path
import sys
import zipfile

import cv2
import numpy as np
import tifffile

W, H = 1360, 1020
MEMBRANES = [  # (x, y, width, height, tilt in degrees)
    (250, 120, 860, 330, 0.8),
    (250, 560, 860, 300, -1.1),
]
LANES = 9  # ladder + 8 samples
# Relative amounts per sample lane for each band row (row y as a fraction of the membrane height).
ROWS = [
    [(0.30, [0.25, 0.45, 0.8, 1.0, 0.2, 0.35, 0.7, 0.9])],                         # membrane 1, Protein A
    [(0.42, [0.9, 0.85, 0.95, 0.9, 0.88, 0.92, 0.9, 0.86])],                       # membrane 2, Actin
]
LADDER = [0.12, 0.22, 0.33, 0.45, 0.58, 0.70, 0.82, 0.92]
EXPOSURES = [2, 10, 30, 60, 120]


def _rotate(image, angle, center, border):
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image, matrix, (W, H), flags=cv2.INTER_LINEAR, borderValue=border)


def signal_map(rng):
    """Band intensity (0..~1.6) in image coordinates, before exposure."""
    signal = np.zeros((H, W), np.float32)
    for (x, y, w, h, tilt), rows in zip(MEMBRANES, ROWS):
        layer = np.zeros_like(signal)
        lane_w = w / (LANES + 0.6)
        for row_y, amounts in rows:
            cy = y + row_y * h
            for i, amount in enumerate(amounts, start=1):
                cx = x + lane_w * (i + 0.8)
                smile = np.linspace(-1, 1, 41) ** 2 * 3
                for k, dx in enumerate(np.linspace(-lane_w * 0.36, lane_w * 0.36, 41)):
                    cv2.circle(layer, (int(cx + dx), int(cy - smile[k])), 4, float(amount) * rng.uniform(0.93, 1.07), -1)
        layer = cv2.GaussianBlur(layer, (0, 0), 2.4)
        signal += _rotate(layer, tilt, (x + w / 2, y + h / 2), 0)
    return signal


def membrane_mask():
    mask = np.zeros((H, W), np.float32)
    for x, y, w, h, tilt in MEMBRANES:
        one = np.zeros_like(mask)
        cv2.rectangle(one, (x, y), (x + w, y + h), 1.0, -1)
        mask = np.maximum(mask, _rotate(one, tilt, (x + w / 2, y + h / 2), 0))
    return cv2.GaussianBlur(mask, (0, 0), 1.0)


def chemi_image(signal, seconds, rng):
    response = 1 - np.exp(-signal * seconds / 45.0)  # film-like saturation curve
    base = 214 + membrane_mask() * 26  # the membrane glows faintly against the tray
    gray = base - response * (base - 8) + rng.normal(0, 2.0, signal.shape)
    gray = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 0.7)
    image = np.repeat(np.clip(gray, 0, 255).astype(np.uint8)[..., None], 3, axis=2)
    saturated = signal * seconds / 45.0 > 2.3
    image[saturated] = (255, 0, 0)  # how ChemiDoc marks saturated pixels
    return image


def colorimetric_image(rng, signal):
    image = np.full((H, W, 3), (58, 60, 62), np.float32)
    image += rng.normal(0, 3, image.shape)
    colors = [(70, 90, 190), (70, 90, 190), (60, 80, 180), (200, 70, 120), (70, 90, 190),
              (70, 90, 190), (90, 160, 90), (70, 90, 190)]
    for x, y, w, h, tilt in MEMBRANES:
        layer = np.zeros((H, W, 3), np.float32)
        mask = np.zeros((H, W), np.float32)
        cv2.rectangle(mask, (x, y), (x + w, y + h), 1.0, -1)
        texture = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 6) * 18
        membrane = np.dstack([226 + texture, 224 + texture, 218 + texture])
        lane_w = w / (LANES + 0.6)
        cx = x + lane_w * 0.8
        for frac, rgb in zip(LADDER, colors):
            cy = int(y + frac * h)
            cv2.rectangle(layer, (int(cx - lane_w * 0.34), cy - 3), (int(cx + lane_w * 0.34), cy + 3), (1, 1, 1), -1)
            band = np.zeros((H, W), np.float32)
            cv2.rectangle(band, (int(cx - lane_w * 0.34), cy - 3), (int(cx + lane_w * 0.34), cy + 3), 1.0, -1)
            band = cv2.GaussianBlur(band, (0, 0), 1.6)[..., None]
            membrane = membrane * (1 - band * 0.75) + np.array(rgb, np.float32) * band * 0.75
        center = (x + w / 2, y + h / 2)
        mask = _rotate(mask, tilt, center, 0)[..., None]
        membrane = np.dstack([_rotate(np.ascontiguousarray(membrane[..., c]), tilt, center, 0) for c in range(3)])
        image = image * (1 - mask) + membrane * mask
    # Faint sample bands on the white-light picture: the current registration aligns on them.
    image -= (np.clip(signal, 0, 1.2) * 70)[..., None] * membrane_mask()[..., None]
    return np.clip(image, 0, 255).astype(np.uint8)


def write(path):
    rng = np.random.default_rng(7)
    signal = signal_map(rng)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        def add(stem, image, mode):
            buffer = io.BytesIO()
            tifffile.imwrite(buffer, image, photometric="rgb")
            archive.writestr(f"ChemiDoc_export/{stem}({mode}).tif", buffer.getvalue())
            archive.writestr(f"ChemiDoc_export/{stem}.scn", f'<!DOCTYPE XML><root><size_pix width="{W}" height="{H}"/>'
                             f'<scan_attributes><exposure_time value="{seconds}"/></scan_attributes></root>')
        for minute, seconds in enumerate(EXPOSURES):
            add(f"2000-01-01 10h{minute:02d}m00s {seconds:.3f}s", chemi_image(signal, seconds, rng), "Chemiluminescence")
        seconds = 0.5
        add("2000-01-01 10h10m00s 0.500s", colorimetric_image(rng, signal), "Colorimetric")
    return path


if __name__ == "__main__":
    print(write(sys.argv[1] if len(sys.argv) > 1 else "promo/data/ChemiDoc_export.zip"))
