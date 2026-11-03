"""Build the DiQuizBuzz logo pack: brand-pack/DiQuizBuzz-Logo-Pack/ (folders of SVG, PNG, PDF, icons, social media
images and a brand guide) and brand-pack/DiQuizBuzz-Logo-Pack.zip.

    pip install fonttools brotli pillow
    python tools/make_logo_pack.py

Microsoft Edge (or Chrome) turns the vector files into PNG and PDF; set EDGE=path\\to\\msedge.exe if it is not in the
usual place. The logo itself is drawn by tools/make_logo.py, so the app and the pack always match.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_logo as M  # noqa: E402

PACK_ROOT = M.ROOT / "brand-pack"
PACK = PACK_ROOT / "DiQuizBuzz-Logo-Pack"
EDGE = os.environ.get("EDGE") or next((p for p in (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium") if Path(p).exists()), None)

NAVY = M.PALETTES["dark"]["bg"]
INK = "#0F172A"
ONE = lambda colour: {**{k: colour for k in ("di", "quiz", "buzz", "bolt", "tag", "red", "red2", "plate")}, "mono": True}   # noqa: E731
VARIANTS = {   # file suffix -> (palette, background to show it on in previews and PDFs)
    "colour": (M.PALETTES["light"], "#FFFFFF"),
    "colour-on-dark": (M.PALETTES["dark"], NAVY),
    "white": (ONE("#FFFFFF"), NAVY),
    "black": (ONE(INK), "#FFFFFF"),
}
TMP = Path(tempfile.mkdtemp(prefix="diqb-"))


def size_of(svg_text: str):
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg_text)
    return float(m.group(1)), float(m.group(2))


def inner(svg_text: str) -> str:
    """The drawing inside an <svg>, without its title, to nest it in a bigger picture."""
    return svg_text.split("</title>", 1)[1].rsplit("</svg>", 1)[0]


def browser(*args, expect: Path):
    """Run headless Edge and wait until it has written `expect` (its launcher returns before the work is done)."""
    if expect.exists():
        expect.unlink()
    # its own profile folder, or an already-open Edge window takes the job over and nothing gets rendered
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                    f"--user-data-dir={TMP / 'profile'}", "--allow-file-access-from-files", *args], timeout=120,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    last, deadline = -1, time.monotonic() + 60
    while time.monotonic() < deadline:
        size = expect.stat().st_size if expect.exists() else -1
        if size > 0 and size == last:
            return
        last = size
        time.sleep(0.4)
    sys.exit(f"The browser did not produce {expect.name}")


def page_for(svg_path: Path, w: int, h: int, bg: str = "transparent", pdf: bool = False) -> Path:
    page = TMP / (svg_path.stem + f"-{w}x{h}{'-pdf' if pdf else ''}.html")
    at_page = f"@page {{ size: {w}px {h}px; margin: 0; }}" if pdf else ""
    page.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{at_page} html,body{{margin:0;padding:0;background:{bg}}}"
                    f"img{{display:block;width:{w}px;height:{h}px}}</style></head><body><img src='{svg_path.as_uri()}'></body></html>",
                    encoding="utf-8")
    return page


def png(svg_path: Path, out: Path, width: int, bg: str | None = None):
    """Render an SVG to a PNG `width` pixels wide (transparent unless `bg` is given)."""
    w, h = size_of(svg_path.read_text(encoding="utf-8"))
    big_w = max(width, 1024) if width < 1024 else width
    big_h = round(big_w * h / w)
    shot = TMP / (out.stem + "-raw.png")
    browser("--default-background-color=00000000", f"--window-size={big_w},{big_h}", f"--screenshot={shot}",
            page_for(svg_path, big_w, big_h, bg or "transparent").as_uri(), expect=shot)
    img = Image.open(shot).convert("RGBA").crop((0, 0, big_w, big_h))
    if big_w != width:
        img = img.resize((width, round(width * h / w)), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return img


def pdf(svg_path: Path, out: Path, bg: str = "transparent"):
    """A vector PDF the size of the artwork, for printers and signwriters."""
    w, h = size_of(svg_path.read_text(encoding="utf-8"))
    out.parent.mkdir(parents=True, exist_ok=True)
    browser("--no-pdf-header-footer", "--print-to-pdf-no-header", f"--print-to-pdf={out}",
            page_for(svg_path, round(w), round(h), bg, pdf=True).as_uri(), expect=out)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build():
    if not EDGE:
        sys.exit("Microsoft Edge or Google Chrome is needed to make the PNG and PDF files (set EDGE=...).")
    if PACK.exists():
        shutil.rmtree(PACK)
    made = []

    # 1 and 2: the logo with its tagline, and the name alone, in four colour versions
    for folder, maker, widths in (("01_Logo-with-tagline", M.logo_svg, (2400, 1200, 600)),
                                  ("02_Wordmark", M.wordmark_svg, (2400, 1200, 400))):
        stem = "DiQuizBuzz-logo" if maker is M.logo_svg else "DiQuizBuzz-wordmark"
        for suffix, (pal, bg) in VARIANTS.items():
            s = write(PACK / folder / "SVG" / f"{stem}-{suffix}.svg", maker(pal))
            for wd in widths:
                png(s, PACK / folder / "PNG" / f"{stem}-{suffix}-{wd}px.png", wd)
            pdf(s, PACK / folder / "PDF" / f"{stem}-{suffix}.pdf", bg if suffix in ("colour-on-dark", "white") else "transparent")
            made.append(s)

    # 3: the square icon, app icons and the browser favicon
    icons = PACK / "03_Icon"
    dark = M.PALETTES["dark"]
    main = write(icons / "SVG" / "DiQuizBuzz-icon.svg", M.mark_svg(NAVY, "#FFFFFF", dark["bolt"]))
    write(icons / "SVG" / "DiQuizBuzz-icon-white.svg", M.mark_svg(None, "#FFFFFF"))
    write(icons / "SVG" / "DiQuizBuzz-icon-black.svg", M.mark_svg(None, INK))
    square = write(icons / "SVG" / "DiQuizBuzz-icon-square.svg", M.mark_svg(NAVY, "#FFFFFF", dark["bolt"], rx=0))
    big = png(main, icons / "PNG" / "DiQuizBuzz-icon-1024px.png", 1024)
    for px in (512, 256, 192, 128, 64, 48, 32, 16):
        big.resize((px, px), Image.LANCZOS).save(icons / "PNG" / f"DiQuizBuzz-icon-{px}px.png", optimize=True)
    sq = png(square, TMP / "square-1024.png", 1024)
    sq.resize((180, 180), Image.LANCZOS).save(icons / "PNG" / "apple-touch-icon-180px.png", optimize=True)   # iOS rounds the corners itself
    for name in ("white", "black"):
        png(icons / "SVG" / f"DiQuizBuzz-icon-{name}.svg", icons / "PNG" / f"DiQuizBuzz-icon-{name}-512px.png", 512)
    big.save(icons / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    shutil.copy(main, icons / "favicon.svg")

    # 4: social media, at each site's own size
    social = PACK / "04_Social-media"
    light = M.PALETTES["light"]
    profile = M.svg(1080, 1080, [f'<rect width="1080" height="1080" fill="{NAVY}"/>',
                                 f'<g transform="translate(205 205) scale(10.47)">{inner(M.mark_svg(None, "#FFFFFF", dark["bolt"]))}</g>'],
                    "DiQuizBuzz")   # the icon kept inside the circle that sites crop profile pictures to
    for name, text in (("DiQuizBuzz-card-1600x900", M.placed(dark, 1600, 900, 0.78, NAVY)),
                       ("DiQuizBuzz-card-light-1600x900", M.placed(light, 1600, 900, 0.78, "#FFFFFF")),
                       ("DiQuizBuzz-post-1080x1080", M.placed(dark, 1080, 1080, 0.84, NAVY)),
                       ("DiQuizBuzz-story-1080x1920", M.placed(dark, 1080, 1920, 0.86, NAVY)),
                       ("DiQuizBuzz-banner-1500x500", M.placed(dark, 1500, 500, 0.62, NAVY)),
                       ("DiQuizBuzz-profile-picture-1080x1080", profile)):
        s = write(social / "SVG" / f"{name}.svg", text)
        w, _ = size_of(text)
        png(s, social / f"{name}.png", int(w))

    # 5: the brand guide (HTML and PDF)
    guide = PACK / "05_Brand-guidelines"
    html = guide_html()
    page = write(guide / "DiQuizBuzz-Brand-Guidelines.html", html)
    gpdf = guide / "DiQuizBuzz-Brand-Guidelines.pdf"
    browser("--no-pdf-header-footer", "--print-to-pdf-no-header", f"--print-to-pdf={gpdf}", page.as_uri(), expect=gpdf)

    write(PACK / "README.txt", README)
    zip_path = PACK_ROOT / "DiQuizBuzz-Logo-Pack.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(PACK.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(PACK_ROOT))
    shutil.rmtree(TMP, ignore_errors=True)
    return zip_path


SWATCHES = [
    ("Teal", "#22D3C5", "The \u201cDi\u201d on dark backgrounds"), ("Deep teal", "#0D9488", "The \u201cDi\u201d on light backgrounds"),
    ("Amber", "#F5A623", "\u201cBuzz\u201d on dark backgrounds"), ("Deep amber", "#E08E00", "\u201cBuzz\u201d on light backgrounds"),
    ("Bolt yellow", "#FFC41F", "The lightning bolt"), ("Buzzer red", "#E53935", "The buzzer button"),
    ("Navy", "#0B1220", "Dark backgrounds"), ("Ink", "#0F172A", "\u201cQuiz\u201d on light backgrounds, text"),
    ("White", "#FFFFFF", "\u201cQuiz\u201d on dark backgrounds"), ("Slate", "#334155", "Tagline on light backgrounds"),
]


def rgb(hexv: str) -> str:
    return ", ".join(str(int(hexv[i:i + 2], 16)) for i in (1, 3, 5))


def guide_html() -> str:
    import base64
    font = base64.b64encode(M.FONT.read_bytes()).decode()       # Inter travels inside the guide, so it looks right anywhere
    rel = "../01_Logo-with-tagline/SVG/"
    tiles = "".join(f'<div class="tile" style="background:{bg}"><img src="{rel}DiQuizBuzz-logo-{v}.svg" alt=""><span style="color:{"#fff" if bg != "#FFFFFF" else "#334155"}">{v.replace("-", " ")}</span></div>'
                    for v, (_, bg) in VARIANTS.items())
    sw = "".join(f'<div class="sw"><i style="background:{h}"></i><b>{nm}</b><code>{h} &middot; RGB {rgb(h)}</code><small>{use}</small></div>' for nm, h, use in SWATCHES)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>DiQuizBuzz brand guidelines</title>
<style>
@font-face {{ font-family: "Inter"; font-weight: 100 900; src: url(data:font/woff2;base64,{font}) format("woff2"); }}
@page {{ size: A4 landscape; margin: 14mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font: 15px/1.5 "Inter", "Segoe UI", system-ui, sans-serif; color: #0F172A; background: #fff; }}
section {{ page-break-after: always; min-height: 170mm; padding: 4mm 2mm; }}
section:last-child {{ page-break-after: auto; }}
h1 {{ font-size: 30px; margin: 0 0 4px; }} h2 {{ font-size: 22px; margin: 0 0 14px; }} p {{ margin: 0 0 10px; color: #334155; max-width: 230mm; }}
.cover {{ display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; gap: 18px; }}
.cover img {{ width: 200mm; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
.tile {{ border: 1px solid #E2E8F0; border-radius: 10px; padding: 18px; display: flex; flex-direction: column; align-items: center; gap: 8px; }}
.tile img {{ width: 100%; max-width: 110mm; }} .tile span {{ font-size: 12px; text-transform: capitalize; }}
.row {{ display: flex; gap: 22px; align-items: center; flex-wrap: wrap; margin: 12px 0; }}
.sws {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }}
.sw {{ border: 1px solid #E2E8F0; border-radius: 10px; padding: 10px; font-size: 12px; display: flex; flex-direction: column; gap: 3px; }}
.sw i {{ display: block; height: 52px; border-radius: 6px; border: 1px solid #E2E8F0; }} .sw code {{ font-size: 11px; color: #334155; }} .sw small {{ color: #64748B; }}
.space {{ display: inline-block; padding: 15mm; border: 2px dashed #94A3B8; border-radius: 6px; }} .space img {{ display: block; width: 110mm; }}
ul {{ margin: 0; padding-left: 18px; color: #334155; }} li {{ margin: 3px 0; }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 26px; }}
.foot {{ margin-top: 12px; font-size: 11px; color: #64748B; }}
</style></head><body>
<section class="cover">
  <img src="{rel}DiQuizBuzz-logo-colour.svg" alt="DiQuizBuzz">
  <h1>Brand guidelines</h1>
  <p>How to use the DiQuizBuzz logo, colours and type so the brand looks the same everywhere.</p>
  <p class="foot">&copy; 2026 DiQuizBuzz &middot; Designed and Developed by Ai with DI (Deepak Ingwale)</p>
</section>
<section>
  <h2>The logo</h2>
  <p><b>Di</b> stands for both &ldquo;the&rdquo; and Deepak Ingwale. The bolt is the dot of the i, the Q carries a play button (the show is on), and the red buzzer sits on &ldquo;Buzz&rdquo;. Tagline: <i>The Quiz Buzz Platform by Di &ndash; Deepak Ingwale</i>.</p>
  <div class="grid">{tiles}</div>
</section>
<section>
  <h2>Which version to use</h2>
  <div class="two">
    <div><ul>
      <li><b>Colour</b>: white or light backgrounds (documents, the app, print).</li>
      <li><b>Colour on dark</b>: navy or dark backgrounds (projector, slides, social posts).</li>
      <li><b>White</b>: photos and coloured backgrounds where the colour logo would clash.</li>
      <li><b>Black</b>: single-colour print, stamps, engraving, fax.</li>
      <li><b>Wordmark</b> (no tagline): headers, small spaces, anywhere under 160&nbsp;px / 40&nbsp;mm wide.</li>
      <li><b>Icon</b>: app icon, browser tab, profile pictures, stickers.</li>
    </ul></div>
    <div class="row">
      <img src="../02_Wordmark/SVG/DiQuizBuzz-wordmark-colour.svg" style="width:95mm" alt="">
      <img src="../03_Icon/SVG/DiQuizBuzz-icon.svg" style="width:30mm" alt="">
      <img src="../03_Icon/SVG/DiQuizBuzz-icon.svg" style="width:12mm" alt="">
      <img src="../03_Icon/SVG/DiQuizBuzz-icon.svg" style="width:6mm" alt="">
    </div>
  </div>
</section>
<section>
  <h2>Colours</h2>
  <p>Teal, amber and the bolt yellow carry the brand. Use the deeper teal and amber on white so the logo stays readable.</p>
  <div class="sws">{sw}</div>
  <h2 style="margin-top:18px">Type</h2>
  <p><b>Inter</b> (free, SIL Open Font License) is the only typeface: ExtraBold for the logo, Medium and Bold for the tagline, Regular to Semibold in the app. Download it from rsms.me/inter.</p>
</section>
<section>
  <h2>Space and size</h2>
  <div class="two">
    <div><p>Keep clear space around the logo at least the height of the &ldquo;Q&rdquo;. Nothing else (text, edges, other logos) goes inside it.</p>
      <div class="space"><img src="{rel}DiQuizBuzz-logo-colour.svg" alt=""></div></div>
    <div><p><b>Smallest sizes</b></p><ul>
      <li>Logo with tagline: 160&nbsp;px or 40&nbsp;mm wide</li>
      <li>Wordmark: 100&nbsp;px or 25&nbsp;mm wide</li>
      <li>Icon: 16&nbsp;px or 5&nbsp;mm</li></ul>
      <p style="margin-top:14px"><b>Please don&rsquo;t</b></p><ul>
      <li>stretch, squash, tilt or rotate the logo</li>
      <li>change its colours or swap the parts around</li>
      <li>add shadows, glows, outlines, gradients or 3D effects</li>
      <li>put the colour logo on busy photos (use the white version)</li>
      <li>retype the name in another font, or reword the tagline</li></ul></div>
  </div>
  <p class="foot">&copy; 2026 DiQuizBuzz &middot; Designed and Developed by Ai with DI (Deepak Ingwale) &middot; deepakingwale.com</p>
</section>
</body></html>
"""


README = """DiQuizBuzz logo pack
The Quiz Buzz Platform by Di - Deepak Ingwale

01_Logo-with-tagline   The full logo. SVG = vector (any size, best quality), PNG = transparent pictures in three widths,
                       PDF = vector for printers.
02_Wordmark            The name without the tagline, for headers and small spaces.
03_Icon                The square icon: app icon, browser tab (favicon.ico / favicon.svg), apple-touch-icon, profile use.
04_Social-media        Ready-made images: 16:9 card (slides, WhatsApp), 1080x1080 post, 1080x1920 story/status,
                       1500x500 banner (X, LinkedIn), 1080x1080 profile picture (safe for round crops).
05_Brand-guidelines    Which version to use, colours, type, clear space and sizes (PDF and HTML).

Colour versions: "colour" for light backgrounds, "colour-on-dark" for navy/dark backgrounds, "white" and "black" for
single-colour use. Font: Inter (SIL Open Font License).

(c) 2026 DiQuizBuzz - Designed and Developed by Ai with DI (Deepak Ingwale) - www.deepakingwale.com
"""


if __name__ == "__main__":
    z = build()
    files = [f for f in PACK.rglob("*") if f.is_file()]
    print(f"{len(files)} files in {PACK}")
    print(f"zip: {z} ({z.stat().st_size // 1024} KB)")
