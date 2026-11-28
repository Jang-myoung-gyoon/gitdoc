"""Regenerate the checked-in GitDoc icons on macOS from static/gitdoc.svg."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "static/gitdoc.svg"
NATIVE = ROOT / "desktop/icons"
WINDOWS = (16, 24, 32, 48, 64, 128, 256)
MAC = (16, 32, 128, 256, 512)


def generate():
    renderer = shutil.which("resvg")
    iconutil = shutil.which("iconutil")
    if renderer is None or iconutil is None:
        raise SystemExit("Install resvg and run this generator on macOS (iconutil is required).")
    NATIVE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gitdoc-icons-") as folder:
        stage = Path(folder)
        frames = {}
        for size in sorted(set(WINDOWS + MAC + tuple(2 * size for size in MAC))):
            frame = stage / f"{size}.png"
            subprocess.run([renderer, "--width", str(size), str(SOURCE), str(frame)], check=True)
            frames[size] = frame
        shutil.copyfile(frames[64], ROOT / "static/gitdoc-64.png")

        iconset = stage / "GitDoc.iconset"
        iconset.mkdir()
        for size in MAC:
            shutil.copyfile(frames[size], iconset / f"icon_{size}x{size}.png")
            shutil.copyfile(frames[size * 2], iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run([iconutil, "--convert", "icns", "--output", str(NATIVE / "GitDoc.icns"), str(iconset)],
                       check=True)

        frames_for_windows = []
        for size in WINDOWS:
            with Image.open(frames[size]) as frame:
                frames_for_windows.append(frame.convert("RGBA"))
        frames_for_windows[-1].save(NATIVE / "gitdoc.ico", sizes=[(size, size) for size in WINDOWS],
                                    append_images=frames_for_windows[:-1])


if __name__ == "__main__":
    generate()
