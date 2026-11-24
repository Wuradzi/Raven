"""Render native_ui/avatar.svg into a square PNG for the macOS app icon. No network, no writes elsewhere.

Usage: .venv/bin/python tools/macos_app_icon.py OUTPUT.png [SIZE]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def visible_bounds(renderer, size):
    """(left, top, right, bottom) of non-transparent pixels when the SVG is fitted into a size x size canvas."""
    import numpy as np
    probe = QImage(size, size, QImage.Format.Format_ARGB32)
    probe.fill(Qt.GlobalColor.transparent)
    painter = QPainter(probe)
    view = renderer.viewBoxF()
    scale = min(size / view.width(), size / view.height())
    renderer.render(painter, QRectF(0, 0, view.width() * scale, view.height() * scale))
    painter.end()
    ptr = probe.constBits()
    alpha = np.frombuffer(ptr, dtype=np.uint8).reshape(size, probe.bytesPerLine() // 4, 4)[:, :size, 3]
    rows, cols = np.where(alpha > 8)
    if not len(rows):
        return 0, 0, size, size
    return int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1


def render(output: Path, size: int = 1024) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - renderer needs a GUI app
    renderer = QSvgRenderer(str(ROOT / "native_ui" / "avatar.svg"))
    if not renderer.isValid():
        raise SystemExit("avatar.svg is not a valid SVG")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # macOS icon grid: ~10% margin, rounded square background in the window's colours.
    margin = size * 0.1
    tile = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    path = QPainterPath()
    path.addRoundedRect(tile, tile.width() * 0.225, tile.height() * 0.225)
    painter.fillPath(path, QColor("#13131f"))
    painter.setClipPath(path)
    # Centre the VISIBLE artwork (ring, bird, beak), not the SVG viewBox: the viewBox is taller than
    # the drawing, which left the bird off the middle of the tile. Render big, crop to the visible
    # pixels, scale to ~68% of the tile and centre.
    probe_size = size * 2
    left, top, right, bottom = visible_bounds(renderer, probe_size)
    probe = QImage(probe_size, probe_size, QImage.Format.Format_ARGB32)
    probe.fill(Qt.GlobalColor.transparent)
    probe_painter = QPainter(probe)
    view = renderer.viewBoxF()
    fit = min(probe_size / view.width(), probe_size / view.height())
    renderer.render(probe_painter, QRectF(0, 0, view.width() * fit, view.height() * fit))
    probe_painter.end()
    crop = probe.copy(left, top, right - left, bottom - top)
    factor = 0.68 * tile.width() / max(crop.width(), crop.height())
    width, height = crop.width() * factor, crop.height() * factor
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(tile.center().x() - width / 2, tile.center().y() - height / 2, width, height), crop)
    painter.end()
    if not image.save(str(output), "PNG"):
        raise SystemExit(f"cannot write {output}")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    render(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) == 3 else 1024)
