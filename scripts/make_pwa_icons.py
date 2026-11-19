"""Draws the generic app icons (a mortarboard on the theme colour) into main_app/static/pwa/. Run once; the PNGs are committed.

    python scripts/make_pwa_icons.py

A college can later get its own icons; these are deliberately plain and carry no name or brand.
"""
from pathlib import Path

from PIL import Image, ImageDraw

THEME = (94, 100, 255)  # --primary-color in dist/css/erpnext-style.css
WHITE = (255, 255, 255)
OUT = Path(__file__).resolve().parents[1] / 'main_app' / 'static' / 'pwa'


def draw(size, safe=1.0, rounded=True):
    """A square icon. `safe` shrinks the drawing into the middle (a maskable icon keeps its art inside the central 80%)."""
    scale = 4  # draw big, then shrink, for smooth edges
    big = size * scale
    image = Image.new('RGBA', (big, big), THEME + (255,))
    d = ImageDraw.Draw(image)

    def p(x, y):  # a point in 0..1 art space, placed inside the safe area
        margin = (1 - safe) / 2
        return ((margin + x * safe) * big, (margin + y * safe) * big)

    d.polygon([p(0.50, 0.24), p(0.90, 0.42), p(0.50, 0.60), p(0.10, 0.42)], fill=WHITE)                      # the board
    d.polygon([p(0.28, 0.54), p(0.50, 0.65), p(0.72, 0.54), p(0.72, 0.72), p(0.50, 0.82), p(0.28, 0.72)], fill=WHITE)  # the cap
    d.line([p(0.86, 0.45), p(0.86, 0.68)], fill=WHITE, width=int(0.018 * big * safe))                           # the tassel
    d.ellipse([*p(0.835, 0.67), *p(0.885, 0.73)], fill=WHITE)
    if rounded:
        mask = Image.new('L', (big, big), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, big, big], radius=int(big * 0.22), fill=255)
        image.putalpha(mask)
    return image.resize((size, size), Image.LANCZOS)


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192).save(OUT / 'icon-192.png', optimize=True)
    draw(512).save(OUT / 'icon-512.png', optimize=True)
    draw(512, safe=0.8, rounded=False).save(OUT / 'icon-maskable-512.png', optimize=True)  # the OS crops it to its own shape
    draw(180, rounded=False).convert('RGB').save(OUT / 'apple-touch-icon.png', optimize=True)  # iOS rounds it itself
    print('icons written to', OUT)
