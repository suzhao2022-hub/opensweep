"""Render the app logo to packaging/opensweep.ico (multi-size) and packaging/opensweep.png."""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from opensweep.gui.widgets import draw_logo  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    draw_logo(size).save(buf, "PNG")
    return bytes(buf.data())


def main() -> None:
    here = Path(__file__).resolve().parent
    app = QGuiApplication(sys.argv)  # noqa: F841 - needed for QPixmap
    images = [png_bytes(s) for s in SIZES]
    # ICO container holding PNG images (supported since Windows Vista)
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, img in zip(SIZES, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(img), offset + len(data))
        data += img
    (here / "opensweep.ico").write_bytes(header + entries + data)
    (here / "opensweep.png").write_bytes(images[-1])
    print("wrote", here / "opensweep.ico")


if __name__ == "__main__":
    main()
