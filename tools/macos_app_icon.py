"""Render web_ui/avatar.svg into a square PNG for the macOS app icon. No network, no writes elsewhere.

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


def render(output: Path, size: int = 1024) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - renderer needs a GUI app
    renderer = QSvgRenderer(str(ROOT / "web_ui" / "avatar.svg"))
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
    view = renderer.viewBoxF()
    scale = min(tile.width() / view.width(), tile.height() / view.height())
    width, height = view.width() * scale, view.height() * scale
    renderer.render(painter, QRectF(tile.center().x() - width / 2, tile.center().y() - height / 2, width, height))
    painter.end()
    if not image.save(str(output), "PNG"):
        raise SystemExit(f"cannot write {output}")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    render(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) == 3 else 1024)
