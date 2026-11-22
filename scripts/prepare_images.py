"""Scale the generated Shelf Life images for the web and make a contact sheet to check them.

    python scripts/prepare_images.py

Reads shelflife/static/img/src/*.png (the originals with their .prompt.txt notes) and writes
trimmed, downscaled WebP files to shelflife/static/img/, plus runs/contact-sheet.png.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "shelflife" / "static" / "img" / "src"
OUT = SRC.parent
SIZES = {"room": 1536, "van": 640, "agent": 520}  # longest side in pixels; sprites default to 360
SPRITE = 360


def main() -> None:
    tiles = []
    for p in sorted(SRC.glob("*.png")):
        im = Image.open(p).convert("RGBA")
        if p.stem != "room":
            box = im.getchannel("A").point(lambda a: 255 if a > 12 else 0).getbbox()
            if box:
                im = im.crop(box)
        side = SIZES.get(p.stem, SPRITE)
        im.thumbnail((side, side), Image.LANCZOS)
        im.save(OUT / f"{p.stem}.webp", "WEBP", quality=88, method=6)
        tiles.append((p.stem, im))
    cell = 260
    cols = 6
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 24)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (name, im) in enumerate(tiles):
        x, y = (i % cols) * cell, (i // cols) * (cell + 24)
        for cx in range(0, cell, 20):  # checkerboard shows transparency
            for cy in range(0, cell, 20):
                if (cx + cy) // 20 % 2:
                    d.rectangle([x + cx, y + cy, x + cx + 19, y + cy + 19], fill=(225, 225, 225))
        t = im.copy()
        t.thumbnail((cell - 10, cell - 10))
        sheet.paste(t, (x + (cell - t.width) // 2, y + (cell - t.height) // 2), t)
        d.text((x + 6, y + cell + 4), f"{name} {im.width}x{im.height}", fill="black")
    (ROOT / "runs").mkdir(exist_ok=True)
    sheet.save(ROOT / "runs" / "contact-sheet.png")
    print(f"{len(tiles)} images -> {OUT}")


if __name__ == "__main__":
    main()
