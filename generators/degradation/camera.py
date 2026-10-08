"""The camera model: a photograph of a receipt lying on a flat surface.

A clean, upright page is warped onto a desk background so it occupies a
sub-region of the frame, perspective-distorted and rotated -- the input a
document-rectification preprocessor must later undo. This is the geometry
Augraphy cannot produce: every Augraphy effect treats the page as a rectangle
square-on to the camera.

The warp uses the same homography library the rectifier uses
(cv2.getPerspectiveTransform / cv2.warpPerspective). Compositing and
photometrics stay in PIL/NumPy. Everything is RGB throughout -- PIL does I/O
and NumPy arrays feed straight to cv2 -- so there is no BGR channel swap.
"""

import io

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def _rot(point: list[float], cx: float, cy: float, degrees: float) -> list[float]:
    """Rotate a point about (cx, cy) by `degrees`."""
    theta = np.radians(degrees)
    x, y = point[0] - cx, point[1] - cy
    return [cx + x * np.cos(theta) - y * np.sin(theta), cy + x * np.sin(theta) + y * np.cos(theta)]


def warp_to_photo(image: Image.Image, warp: dict, rng: np.random.Generator) -> Image.Image:
    """Warp a flat page onto a desk background, as if photographed off-axis.

    Args:
        image: The (already ink/paper-augmented) flat page.
        warp: Tier warp parameters -- `foreshorten`, `rotation_deg` and
            `margin`, each a [min, max] pair.
        rng: Seeded generator; all randomness is drawn from it.

    Returns:
        An RGB frame larger than the input, with the page occupying a
        perspective-distorted sub-region over a desk background.
    """
    page = image.convert("RGB")
    w, h = page.size

    margin_lo, margin_hi = warp["margin"]
    pad_x = int(w * rng.uniform(margin_lo, margin_hi))
    pad_y = int(h * rng.uniform(margin_lo, margin_hi))
    cw, ch = w + 2 * pad_x, h + 2 * pad_y

    # Flat desk: muted tone, gentle lighting gradient, faint noise.
    base = np.array([rng.uniform(150, 200), rng.uniform(140, 190), rng.uniform(125, 175)])
    bg = np.ones((ch, cw, 3)) * base
    gx = np.linspace(rng.uniform(-25, 0), rng.uniform(0, 25), cw)[None, :, None]
    gy = np.linspace(rng.uniform(-20, 0), rng.uniform(0, 20), ch)[:, None, None]
    bg = np.clip(bg + gx + gy + rng.normal(0, 3, (ch, cw, 3)), 0, 255)

    # Destination quad: foreshorten one edge, then rotate the whole page.
    fore_lo, fore_hi = warp["foreshorten"]
    f = rng.uniform(fore_lo, fore_hi)
    edge = int(rng.integers(0, 4))
    q = [[0.0, 0.0], [float(w), 0.0], [float(w), float(h)], [0.0, float(h)]]  # TL TR BR BL
    if edge == 0:  # top edge away
        q[0][0] += w * f
        q[1][0] -= w * f
    elif edge == 1:  # right edge away
        q[1][1] += h * f
        q[2][1] -= h * f
    elif edge == 2:  # bottom edge away
        q[3][0] += w * f
        q[2][0] -= w * f
    else:  # left edge away
        q[0][1] += h * f
        q[3][1] -= h * f

    rot_lo, rot_hi = warp["rotation_deg"]
    degrees = rng.uniform(rot_lo, rot_hi)
    q = [_rot(p, w / 2, h / 2, degrees) for p in q]

    ox = pad_x + rng.uniform(-pad_x * 0.3, pad_x * 0.3)
    oy = pad_y + rng.uniform(-pad_y * 0.3, pad_y * 0.3)
    dst = np.array([[x + ox, y + oy] for x, y in q], dtype=np.float32)
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)

    m = cv2.getPerspectiveTransform(src, dst)
    rgba = np.dstack([np.array(page), np.full((h, w), 255, np.uint8)])
    warped = cv2.warpPerspective(
        rgba,
        m,
        (cw, ch),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    alpha = (warped[:, :, 3].astype(np.float32) / 255.0)[:, :, None]

    # Drop shadow under the page.
    shadow = cv2.GaussianBlur(warped[:, :, 3], (0, 0), max(w, h) * 0.02) * 0.45
    offset = int(max(w, h) * 0.015)
    shadow = np.roll(np.roll(shadow, offset, axis=0), offset, axis=1)[:, :, None] / 255.0
    bg = bg * (1 - shadow) + np.array([25, 22, 20]) * shadow

    composite = bg * (1 - alpha) + warped[:, :, :3].astype(np.float32) * alpha
    return Image.fromarray(np.clip(composite, 0, 255).astype(np.uint8), "RGB")


def apply_photometrics(image: Image.Image, camera: dict, rng: np.random.Generator) -> Image.Image:
    """Apply lens and sensor artefacts to a whole frame.

    Runs after the warp: blur, sensor noise and JPEG blocking are properties of
    the camera and the file, not of the paper.

    Args:
        image: The composited frame.
        camera: Tier camera parameters -- `blur`, `noise_sigma` and `jpeg`,
            each a [min, max] pair.
        rng: Seeded generator; all randomness is drawn from it.

    Returns:
        The photographed-looking frame, same dimensions as the input.
    """
    frame = image.convert("RGB")
    frame = ImageEnhance.Brightness(frame).enhance(rng.uniform(0.92, 1.05))
    frame = ImageEnhance.Contrast(frame).enhance(rng.uniform(0.90, 1.0))

    blur_lo, blur_hi = camera["blur"]
    frame = frame.filter(ImageFilter.GaussianBlur(rng.uniform(blur_lo, blur_hi)))

    noise_lo, noise_hi = camera["noise_sigma"]
    sigma = rng.uniform(noise_lo, noise_hi)
    arr = np.array(frame).astype(np.int16)
    arr = arr + rng.normal(0, sigma, arr.shape).astype(np.int16)
    frame = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    jpeg_lo, jpeg_hi = camera["jpeg"]
    buf = io.BytesIO()
    frame.save(buf, format="JPEG", quality=int(rng.integers(jpeg_lo, jpeg_hi + 1)))
    buf.seek(0)
    return Image.open(buf).convert("RGB")
