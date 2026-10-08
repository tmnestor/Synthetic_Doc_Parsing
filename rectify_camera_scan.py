"""Offline rectification — undo the camera-scan perspective of receipt photos.

The inverse of ``generators/degradation/camera.py``: given a phone-photo-style
receipt (perspective-distorted, rotated, on a flat background), detect the
receipt quadrilateral and apply a 4-point perspective transform to produce an
upright, cropped, frontal receipt. This runs OFFLINE (a preprocessing pass), so
the downstream VLM receives already-rectified images and the inference env needs
no OpenCV.

Pipeline: grayscale -> blur -> Canny edges -> dilate -> largest external contour
-> 4-point polygon -> cv2.getPerspectiveTransform / warpPerspective. Uses the
SAME homography library generators/degradation/camera.py warps with.

The two are *approximate*, not exact, inverses: this pass re-estimates the quad
from pixels rather than reusing the stored homography, and sizes its output to
the detected edge lengths, while the blur, sensor noise and JPEG steps are not
invertible at all. That is moot under value-F1 scoring, which reads field values
rather than positions, and matters only for spatial round-trip validation.

**Fail-open**: if no convincing quad is found (no 4-gon, too small, or too large),
the original image is returned unchanged. A missed rectification is cheap; a wrong
crop that drops receipt content is a regression — when in doubt, do nothing.

Usage:
    python rectify_camera_scan.py <in.png> <out.png>     # single
    python rectify_camera_scan.py --batch <output_root>  # degraded/ -> rectified/
"""

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def order_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 (x, y) points as top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # top-left  (min x+y)
    rect[2] = pts[np.argmax(s)]  # bottom-right (max x+y)
    d = np.diff(pts, axis=1)[:, 0]  # y - x
    rect[1] = pts[np.argmin(d)]  # top-right (min y-x)
    rect[3] = pts[np.argmax(d)]  # bottom-left (max y-x)
    return rect


def four_point_transform(rgb: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Warp the quad in *rgb* to an upright rectangle sized by its edge lengths."""
    rect = order_points(quad)
    tl, tr, br, bl = rect
    width = max(int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))), 1)
    height = max(int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))), 1)
    dst = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype="float32",
    )
    m = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(rgb, m, (width, height), flags=cv2.INTER_CUBIC)


def detect_document_quad(
    rgb: np.ndarray, *, min_area_frac: float = 0.15, max_area_frac: float = 0.99
) -> np.ndarray | None:
    """Return the receipt's 4 corners (float32 4x2) or None if none is convincing."""
    h, w = rgb.shape[:2]
    area = float(h * w)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(gray, 50, 150)
    edged = cv2.dilate(edged, np.ones((5, 5), np.uint8), iterations=2)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if not (min_area_frac * area <= cv2.contourArea(contour) <= max_area_frac * area):
        return None

    peri = cv2.arcLength(contour, True)
    for eps in (0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(contour, eps * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype("float32")
    # Fallback: minimum-area rotated rectangle (corrects rotation, not perspective).
    box = cv2.boxPoints(cv2.minAreaRect(contour))
    if min_area_frac * area <= cv2.contourArea(box.astype("float32")) <= max_area_frac * area:
        return box.astype("float32")
    return None


def rectify(in_path: Path, out_path: Path) -> bool:
    """Rectify one receipt photo. Returns True if a quad was found and corrected."""
    image = Image.open(in_path).convert("RGB")
    rgb = np.asarray(image)
    quad = detect_document_quad(rgb)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if quad is None:
        image.save(out_path, format="PNG")  # fail-open: pass through unchanged
        return False
    Image.fromarray(four_point_transform(rgb, quad)).save(out_path, format="PNG")
    return True


def batch(root: Path) -> tuple[int, int]:
    """Rectify root/degraded/receipts/*.png -> root/rectified/receipts/. Returns
    (detected, total)."""
    src_dir = root / "degraded" / "receipts"
    out_dir = root / "rectified" / "receipts"
    files = sorted(src_dir.glob("*.png"))
    detected = sum(rectify(f, out_dir / f.name) for f in files)
    return detected, len(files)


if __name__ == "__main__":
    if sys.argv[1] == "--batch":
        n_ok, n = batch(Path(sys.argv[2]))
        print(f"rectified {n_ok}/{n} receipts -> {Path(sys.argv[2]) / 'rectified' / 'receipts'}")
    else:
        ok = rectify(Path(sys.argv[1]), Path(sys.argv[2]))
        print(f"{'rectified' if ok else 'passed through (no quad)'}: {sys.argv[2]}")
