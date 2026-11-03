"""Build the DiQuizBuzz logo files from the bundled Inter font (letters become vector outlines, so the SVGs look the
same everywhere, with no font needed).

    pip install fonttools brotli          (only needed to run this script; the app itself does not use them)
    python tools/make_logo.py

Writes quickz/static/brand/*.svg and quickz/static/favicon.svg. Inter is under the SIL Open Font License, which allows
turning its glyphs into outlines inside a logo.

The design: "Di" in teal with a lightning bolt for the dot of the i, a "Q" with a play button cut into it, "uiz", and
"Buzz" in amber with a red quiz buzzer sitting on the "u". Flat colours only (no glow, no gradients).
"""
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "quickz" / "static" / "fonts" / "inter.woff2"
OUT = ROOT / "quickz" / "static" / "brand"
TAGLINE = ("The Quiz Buzz Platform by ", "Di – Deepak Ingwale")

PALETTES = {
    # for dark backgrounds (the projector in dark mode, slides, social images)
    "dark": {"di": "#22D3C5", "quiz": "#FFFFFF", "buzz": "#F5A623", "bolt": "#FFC41F", "tag": "#E2E8F0",
             "red": "#E53935", "red2": "#B3261E", "plate": "#334155", "bg": "#0B1220"},
    # for white / light backgrounds (the app's default look, documents, print)
    "light": {"di": "#0D9488", "quiz": "#0F172A", "buzz": "#E08E00", "bolt": "#F2A900", "tag": "#334155",
              "red": "#E53935", "red2": "#B3261E", "plate": "#334155", "bg": "#FFFFFF"},
}


class Face:
    def __init__(self, weight: int):
        self.font = instantiateVariableFont(TTFont(FONT), {"wght": weight}, updateFontNames=False)
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.hmtx = self.font["hmtx"]
        self.upm = self.font["head"].unitsPerEm
        self.cap = self.font["OS/2"].sCapHeight
        self.xh = self.font["OS/2"].sxHeight

    def glyph(self, ch: str) -> str:
        return self.cmap[ord(ch)]

    def bounds(self, ch: str):
        pen = BoundsPen(self.glyphs)
        self.glyphs[self.glyph(ch)].draw(pen)
        return pen.bounds

    def run(self, text: str, x: float, base: float, size: float, track: float = 0.0):
        """Outline `text` with its baseline at `base`; returns (path data, x after the last letter)."""
        s = size / self.upm
        parts = []
        for ch in text:
            name = self.glyph(ch)
            pen = SVGPathPen(self.glyphs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
            self.glyphs[name].draw(TransformPen(pen, (s, 0, 0, -s, x, base)))
            parts.append(pen.getCommands())
            x += self.hmtx[name][0] * s + track
        return " ".join(parts), x

    def width(self, text: str, size: float, track: float = 0.0) -> float:
        return self.run(text, 0, 0, size, track)[1] - track


def n(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def bolt(cx: float, top: float, h: float) -> str:
    """A lightning bolt, `h` tall, centred on cx."""
    w = h * 0.62
    pts = [(0.66, 0), (0.08, 0.57), (0.45, 0.57), (0.30, 1), (0.93, 0.39), (0.56, 0.39), (0.76, 0)]
    return "M" + " L".join(f"{n(cx - w / 2 + px * w)} {n(top + py * h)}" for px, py in pts) + " Z"


def wordmark(bold: Face, size: float, c: dict, x0: float = 0.0, base: float = 0.0):
    """The DiQuizBuzz wordmark. Returns (svg elements, right edge, top, bottom)."""
    s = size / bold.upm
    cap, xh = bold.cap * s, bold.xh * s
    track = -0.018 * size
    els = []

    # "Di": teal, with a bolt where the dot of the i would be
    d_path, x = bold.run("D", x0, base, size, track)
    i_x = x
    i_path, x = bold.run("ı", x, base, size, track)
    els.append(f'<path fill="{c["di"]}" d="{d_path} {i_path}"/>')
    ib = bold.bounds("ı")
    stem = i_x + (ib[0] + ib[2]) / 2 * s
    bh = (cap - xh) + 0.22 * size
    els.append(f'<path fill="{c["bolt"]}" d="{bolt(stem + 0.012 * size, base - xh - 0.06 * size - bh, bh)}"/>')

    # "Q": a solid disc with a play button cut out of it, and a tail
    x += 0.035 * size
    r = cap * 0.53
    cx, cy = x + r, base - cap / 2
    tri = [(cx - r * 0.26, cy - r * 0.44), (cx + r * 0.50, cy), (cx - r * 0.26, cy + r * 0.44)]
    disc = (f"M{n(cx - r)} {n(cy)} A{n(r)} {n(r)} 0 1 0 {n(cx + r)} {n(cy)} A{n(r)} {n(r)} 0 1 0 {n(cx - r)} {n(cy)} Z "
            f"M{n(tri[0][0])} {n(tri[0][1])} L{n(tri[1][0])} {n(tri[1][1])} L{n(tri[2][0])} {n(tri[2][1])} Z")
    els.append(f'<path fill="{c["quiz"]}" fill-rule="evenodd" d="{disc}"/>')
    # the tail starts inside the disc and ends square, so it reads as the stroke of a Q, not the handle of a magnifier
    tw = 0.15 * size
    els.append(f'<path stroke="{c["quiz"]}" stroke-width="{n(tw)}" stroke-linecap="butt" fill="none" '
               f'd="M{n(cx + r * 0.45)} {n(cy + r * 0.40)} L{n(cx + r * 1.08)} {n(cy + r * 1.02)}"/>')
    x = cx + r + 0.05 * size

    # "uiz"
    p, x = bold.run("uiz", x, base, size, track)
    els.append(f'<path fill="{c["quiz"]}" d="{p}"/>')

    # "Buzz": amber, with a red buzzer resting on the u
    x += 0.03 * size
    p1, ux = bold.run("B", x, base, size, track)
    p2, zx = bold.run("u", ux, base, size, track)
    p3, x = bold.run("zz", zx, base, size, track)
    els.append(f'<path fill="{c["buzz"]}" d="{p1} {p2} {p3}"/>')
    ub = bold.bounds("u")
    ul, ur = ux + ub[0] * s, ux + ub[2] * s
    uw, ucx = ur - ul, (ul + ur) / 2
    plate_h, gap = 0.055 * size, 0.018 * size
    plate_top = base - xh - gap - plate_h
    pw = uw * 1.02
    els.append(f'<rect fill="{c["plate"]}" x="{n(ucx - pw / 2)}" y="{n(plate_top)}" width="{n(pw)}" height="{n(plate_h)}" rx="{n(plate_h / 2)}"/>')
    dw, dh = uw * 0.74, 0.15 * size
    # in a one-colour version the dome floats just above the plate, so the button still reads as a button
    dome_base = plate_top - (0.024 * size if c.get("mono") else -0.5)
    ctrl = (dome_base if c.get("mono") else plate_top) - dh * 1.25
    dome = (f"M{n(ucx - dw / 2)} {n(dome_base)} C{n(ucx - dw / 2)} {n(ctrl)} "
            f"{n(ucx + dw / 2)} {n(ctrl)} {n(ucx + dw / 2)} {n(dome_base)} Z")
    els.append(f'<path fill="{c["red"]}" d="{dome}"/>')
    if not c.get("mono"):
        rim = 0.028 * size
        els.append(f'<rect fill="{c["red2"]}" x="{n(ucx - dw / 2)}" y="{n(plate_top - rim + 0.5)}" width="{n(dw)}" height="{n(rim)}"/>')
    # "it's buzzing": two short arcs on each side of the button
    sw = 0.024 * size
    arcs = []
    ay = plate_top - dh * 0.45
    for side in (-1, 1):
        for k, rad in enumerate((0.11 * size, 0.18 * size)):
            ax = ucx + side * (dw / 2 + 0.02 * size)
            a0 = (ax + side * rad * 0.42, ay - rad * 0.78)
            a1 = (ax + side * rad * 0.42, ay + rad * 0.78)
            sweep = 1 if side > 0 else 0
            arcs.append(f"M{n(a0[0])} {n(a0[1])} A{n(rad)} {n(rad)} 0 0 {sweep} {n(a1[0])} {n(a1[1])}")
    els.append(f'<path stroke="{c["buzz"]}" stroke-width="{n(sw)}" stroke-linecap="round" fill="none" d="{" ".join(arcs)}"/>')

    top = base - xh - 0.06 * size - bh
    bottom = cy + r * 1.06 + tw / 2
    return els, x - track, top, bottom


def tagline(face: Face, tag_bold: Face, size: float, c: dict, cx: float, base: float):
    a, b = TAGLINE
    total = face.width(a, size) + tag_bold.width(b, size)
    p1, x = face.run(a, cx - total / 2, base, size)
    p2, _ = tag_bold.run(b, x, base, size)
    return [f'<path fill="{c["tag"]}" d="{p1}"/>', f'<path fill="{c["di"]}" d="{p2}"/>'], total


def svg(w: float, h: float, body: list[str], title: str, bg: str | None = None) -> str:
    rect = f'<rect width="{n(w)}" height="{n(h)}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n(w)} {n(h)}" width="{n(w)}" height="{n(h)}" role="img" '
            f'aria-label="{title}"><title>{title}</title>{rect}{"".join(body)}</svg>\n')


TITLE = "DiQuizBuzz – The Quiz Buzz Platform by Di – Deepak Ingwale"
_FACES = None


def faces():
    """The three weights the logo uses (made once: instancing the variable font takes a moment)."""
    global _FACES
    if _FACES is None:
        _FACES = (Face(800), Face(500), Face(700))
    return _FACES


def compose(c: dict, size: float = 200, pad: float = 12) -> dict:
    """Lay out the logo in palette `c`. Returns the SVG pieces plus the sizes the callers need to place them."""
    bold, mid, semi = faces()
    els, right, top, bottom = wordmark(bold, size, c, x0=pad, base=0)
    width = right + pad
    shift = -top + pad
    word_h = bottom - top + 2 * pad
    tsize = size * 0.2
    while True:
        tag, tw = tagline(mid, semi, tsize, c, width / 2, bottom + tsize * 2.0)
        if tw <= width - 2 * pad:
            break
        tsize *= 0.96
    full_h = bottom + tsize * 2.0 + tsize * 0.45 - top + 2 * pad
    return {"els": els, "tag": tag, "width": width, "shift": shift, "word_h": word_h, "full_h": full_h}


def wordmark_svg(c: dict) -> str:
    L = compose(c)
    return svg(L["width"], L["word_h"], [f'<g transform="translate(0 {n(L["shift"])})">{"".join(L["els"])}</g>'], "DiQuizBuzz")


def logo_svg(c: dict) -> str:
    L = compose(c)
    return svg(L["width"], L["full_h"], [f'<g transform="translate(0 {n(L["shift"])})">{"".join(L["els"])}{"".join(L["tag"])}</g>'], TITLE)


def placed(c: dict, w: float, h: float, frac: float, bg: str | None, with_tag: bool = True, dy: float = 0.0) -> str:
    """The logo centred on a `w` x `h` canvas, `frac` of its width wide (social cards, banners, posts)."""
    L = compose(c)
    lh = L["full_h"] if with_tag else L["word_h"]
    k = (w * frac) / L["width"]
    tx, ty = (w - L["width"] * k) / 2, (h - lh * k) / 2 + L["shift"] * k + dy
    body = "".join(L["els"]) + ("".join(L["tag"]) if with_tag else "")
    return svg(w, h, [f'<g transform="translate({n(tx)} {n(ty)}) scale({k:.4f})">{body}</g>'], TITLE, bg)


def mark_svg(bg: str | None = None, fg: str = "#FFFFFF", bolt_fill: str | None = None, size: int = 64, rx: float = 14) -> str:
    """The square icon: the play-button Q with the bolt. With `bg` None it is transparent (for single-colour use)."""
    k = size / 64
    cx, cy, r = 30 * k, 33 * k, 19 * k
    tri = [(cx - r * 0.26, cy - r * 0.44), (cx + r * 0.50, cy), (cx - r * 0.26, cy + r * 0.44)]
    body = []
    if bg:
        body.append(f'<rect width="{size}" height="{size}" rx="{n(rx * k)}" fill="{bg}"/>')
    body += [
        f'<path fill="{fg}" fill-rule="evenodd" d="M{n(cx - r)} {n(cy)} A{n(r)} {n(r)} 0 1 0 {n(cx + r)} {n(cy)} A{n(r)} {n(r)} 0 1 0 {n(cx - r)} {n(cy)} Z '
        f'M{n(tri[0][0])} {n(tri[0][1])} L{n(tri[1][0])} {n(tri[1][1])} L{n(tri[2][0])} {n(tri[2][1])} Z"/>',
        f'<path stroke="{fg}" stroke-width="{n(7 * k)}" stroke-linecap="butt" d="M{n(cx + r * 0.45)} {n(cy + r * 0.40)} L{n(cx + r * 1.08)} {n(cy + r * 1.02)}"/>',
        f'<path fill="{bolt_fill or fg}" d="{bolt(50 * k, 4 * k, 21 * k)}"/>',
    ]
    return svg(size, size, body, "DiQuizBuzz")


def build():
    """The files the app itself uses."""
    OUT.mkdir(parents=True, exist_ok=True)
    light, dark = PALETTES["light"], PALETTES["dark"]
    written = [
        ("diquizbuzz-wordmark.svg", wordmark_svg(light)),
        ("diquizbuzz-wordmark-dark.svg", wordmark_svg(dark)),
        ("diquizbuzz-logo.svg", logo_svg(light)),
        ("diquizbuzz-logo-dark.svg", logo_svg(dark)),
        ("diquizbuzz-card.svg", placed(dark, 1600, 900, 0.78, dark["bg"])),     # 16:9 on navy, like the original artwork
        ("diquizbuzz-mark.svg", mark_svg(dark["bg"], "#FFFFFF", dark["bolt"])),
    ]
    for fname, text in written:
        (OUT / fname).write_text(text, encoding="utf-8")
    (ROOT / "quickz" / "static" / "favicon.svg").write_text(written[-1][1], encoding="utf-8")
    return [f for f, _ in written]


if __name__ == "__main__":
    for f in build():
        print("wrote", OUT / f)
