"""Pictures of 3D models (Nov 25): "can I see it before I print it?"

An STL (binary or text) becomes a PNG with three views (3D, front, side) and its size in mm, drawn with numpy and
Pillow, which E.V.A. already has: no new packages, no network, nothing runs from the file. A depth buffer, simple
light and dark lines on creases: good enough to judge a shape, and for the task to look at its own work. All views
share one scale, so the parts compare.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

MAX_TRIANGLES = 400_000
BG, INK = (18, 20, 28), (225, 228, 240)
BASE = np.array([120, 170, 255], dtype=float)        # E.V.A. blue


def load_stl(path: str | Path) -> np.ndarray:
    """(n, 3, 3) float array of triangles in mm."""
    data = Path(path).read_bytes()
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 0 < n <= MAX_TRIANGLES and len(data) == 84 + 50 * n:          # binary
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                                count=n, offset=84)
            return rec["v"].astype(float)
    text = data.decode("ascii", errors="ignore")
    if "vertex" not in text:
        raise ValueError("not an STL file")
    nums = [line.split()[1:4] for line in text.splitlines() if line.strip().startswith("vertex")]
    v = np.array(nums[: MAX_TRIANGLES * 3], dtype=float)
    return v[: len(v) // 3 * 3].reshape(-1, 3, 3)


def _rot(yaw: float, pitch: float) -> np.ndarray:
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    return rx @ rz


def _view(tri: np.ndarray, rot: np.ndarray, size: int, span: float) -> np.ndarray:
    """One view as an RGB array: a depth buffer, simple light, and dark lines on creases and outlines.
    After rotation: x right, y away from you (depth), z up."""
    p = tri @ rot.T
    scale = (size - 30) / max(span, 1e-6)
    sx = size / 2 + p[:, :, 0] * scale
    sy = size / 2 - p[:, :, 2] * scale
    dz = p[:, :, 1]
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-12
    n[ok] /= ln[ok, None]
    light = np.array([-0.45, -0.65, 0.6])
    light /= np.linalg.norm(light)
    shade = 0.28 + 0.72 * np.abs(n @ light)             # two-sided: a flipped normal still looks right
    depth = np.full((size, size), np.inf)
    face = np.full((size, size), -1, dtype=np.int64)
    for i in np.nonzero(ok)[0]:
        x, y, z = sx[i], sy[i], dz[i]
        x0, x1 = max(int(np.floor(x.min())), 0), min(int(np.ceil(x.max())), size - 1)
        y0, y1 = max(int(np.floor(y.min())), 0), min(int(np.ceil(y.max())), size - 1)
        if x0 > x1 or y0 > y1:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(d) < 1e-9:
            continue
        a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / d
        b = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / d
        c = 1 - a - b
        inside = (a >= -1e-6) & (b >= -1e-6) & (c >= -1e-6)
        if not inside.any():
            continue
        zz = a * z[0] + b * z[1] + c * z[2]
        win = depth[y0:y1 + 1, x0:x1 + 1]
        nearer = inside & (zz < win)
        win[nearer] = zz[nearer]
        face[y0:y1 + 1, x0:x1 + 1][nearer] = i
    img = np.empty((size, size, 3))
    img[:] = BG
    hit = face >= 0
    img[hit] = BASE * shade[face[hit]][:, None]
    # lines: outline, creases (neighbouring faces facing other ways) and depth steps
    nrm = np.zeros((size, size, 3))
    nrm[hit] = n[face[hit]]
    dep = np.where(hit, depth, 0.0)
    edge = np.zeros((size, size), dtype=bool)
    for ax in (0, 1):
        h2 = np.roll(hit, 1, axis=ax)
        crease = np.abs(np.sum(nrm * np.roll(nrm, 1, axis=ax), axis=2)) < 0.9
        step = np.abs(dep - np.roll(dep, 1, axis=ax)) > 0.02 * span
        edge |= (hit != h2) | (hit & h2 & (crease | step))
    img[edge] = (20, 30, 55)
    return np.clip(img, 0, 255).astype(np.uint8)


def render(path: str | Path, out: str | Path | None = None, size: int = 420) -> dict:
    """PNG with a 3D view, the front and the side, plus the size. {"png", "size_mm", "triangles"}."""
    from PIL import Image, ImageDraw

    tri = load_stl(path)
    if not len(tri):
        raise ValueError("the STL has no triangles")
    pts = tri.reshape(-1, 3)
    dims = pts.max(0) - pts.min(0)
    tri = tri - (pts.min(0) + dims / 2)
    if len(tri) > 150_000:                              # a picture doesn't need every triangle
        tri = tri[:: int(np.ceil(len(tri) / 150_000))]
    span = float(np.linalg.norm(dims)) or 1.0
    img = Image.new("RGB", (size * 3, size + 40), BG)
    views = [("3D", _rot(np.radians(-35), np.radians(25))),       # from above, front left
             ("front", _rot(0.0, 0.0)),
             ("side", _rot(np.radians(90), 0.0))]
    for k, (label, r) in enumerate(views):
        img.paste(Image.fromarray(_view(tri, r, size, span)), (k * size, 0))
    d = ImageDraw.Draw(img)
    for k, (label, _) in enumerate(views):
        d.text((k * size + 10, 8), label, fill=INK)
    name = Path(path).name
    d.text((10, size + 12), f"{name}   {dims[0]:.0f} x {dims[1]:.0f} x {dims[2]:.0f} mm   "
                            f"({len(tri)} triangles)", fill=INK)
    out = Path(out) if out else Path(path).with_suffix(".png")
    img.save(out)
    return {"png": str(out), "size_mm": [round(float(x), 1) for x in dims], "triangles": int(len(tri))}
