"""Renderiza o logo SVG do Rever como PNG e ícone Windows multirresolução."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "pdf_reader" / "assets" / "icons"


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    output = QByteArray()
    buffer = QBuffer(output)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Falha ao renderizar o ícone de {size}px")
    return bytes(output)


def main() -> None:
    renderer = QSvgRenderer(str(ICONS / "rever.svg"))
    if not renderer.isValid():
        raise RuntimeError("SVG do Rever inválido")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [render_png(renderer, size) for size in sizes]
    (ICONS / "rever.png").write_bytes(render_png(renderer, 512))
    entries = []
    offset = 6 + 16 * len(sizes)
    for size, data in zip(sizes, images):
        encoded_size = 0 if size == 256 else size
        entries.append(struct.pack("<BBBBHHII", encoded_size, encoded_size, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    (ICONS / "rever.ico").write_bytes(
        struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(images)
    )


if __name__ == "__main__":
    main()
