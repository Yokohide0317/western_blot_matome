import io
import zipfile

import cv2
import numpy as np
import tifffile


def images(seed=1, two=True, shift=0, ladder=True):
    rng = np.random.default_rng(seed)
    h, w = 900, 1100
    marker = np.full((h, w, 3), 45, np.uint8)
    exposure = np.full_like(marker, 45)
    for x, y, angle in ([(180, 80, 1), (180, 480, -2)] if two else [(180, 80, 1)]):
        cv2.rectangle(marker, (x, y), (920, y + 300), (225, 225, 225), -1)
        cv2.rectangle(exposure, (x, y), (920, y + 300), (225, 225, 225), -1)
        for yy in (y + 95, y + 205):
            for xx in range(x + 150, x + 700, 75):
                dy = round((xx - x - 400) * np.tan(np.deg2rad(-angle)))
                shade = int(rng.integers(35, 115))
                for img in (marker, exposure):
                    cv2.ellipse(img, (xx, yy + dy), (20, 7), 0, 0, 360, (shade,) * 3, -1)
        if ladder:
            for yy in range(y + 45, y + 270, 30):
                cv2.rectangle(marker, (x + 25, yy), (x + 55, yy + 5), (65, 65, 65), -1)
    if shift:
        exposure = cv2.warpAffine(exposure, np.float32([[1, 0, shift], [0, 1, 0]]), (w, h),
                                  borderValue=(45, 45, 45))
    return marker, exposure


def make_zip(path, sessions=1, two=True, stray=False, markers=True, ladder=True):
    with zipfile.ZipFile(path, 'w') as z:
        for session in range(sessions):
            marker, exposure = images(seed=42 + session, two=two, ladder=ladder)
            # Distinct sessions occupy different locations, so they cannot cross-register.
            if session:
                marker = np.roll(marker, -130, axis=1)
                exposure = np.roll(exposure, -130, axis=1)
            _write(z, f'series{session}', f'2000-01-01 12h{session:02d}m16s', exposure, 'Chemiluminescence')
            if markers:
                _write(z, f'marker{session}', f'2000-01-01 13h{session:02d}m16s', marker, 'Colorimetric')
        if stray:
            _, exposure = images(seed=99, two=two, shift=300)
            _write(z, 'stray', '2000-01-01 12h59m16s', exposure, 'Chemiluminescence')


def _write(z, folder, timestamp, image, modality):
    stem = f'{timestamp} 60.000s'
    buffer = io.BytesIO()
    tifffile.imwrite(buffer, image, photometric='rgb')
    z.writestr(f'{folder}/{stem}({modality}).tif', buffer.getvalue())
    z.writestr(f'{folder}/{stem}.scn', '<!DOCTYPE XML><root><size_pix width="1100" height="900"/>'
               '<scan_attributes><exposure_time value="60"/></scan_attributes></root>')


def scoring_signal():
    """One strong lane row, a continuous smear, and a faint lane row."""
    signal = np.zeros((400, 700), np.float32)
    for x in range(100, 650, 70):
        cv2.ellipse(signal, (x, 90), (18, 6), 0, 0, 360, .8, -1)
        cv2.ellipse(signal, (x, 315), (18, 6), 0, 0, 360, .04, -1)
    signal[175:260, 70:640] = .45
    return cv2.GaussianBlur(signal, (0, 0), 1)
