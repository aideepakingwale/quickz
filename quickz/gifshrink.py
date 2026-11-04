"""Shrink a too-big animated GIF so it fits the picture limit, keeping it animated.

A browser cannot do this (drawing a GIF on a canvas keeps only one frame), so the server does it. It has to be gentle on a
small server that is also running a live game, so it:
  - never holds the whole GIF decoded: frames are read one at a time and only the small, palette-based result is kept;
  - picks the setting by encoding a few sample frames and estimating the total, instead of trying every setting;
  - uses ONE colour palette for the whole animation, so a part of the picture that does not move stays identical from frame to
    frame and the GIF only stores what changes (a separate palette per frame makes the file bigger, not smaller);
  - stops with a clear message if the picture is too big or long, or the work takes too long.
Settings go from gentle to strong: a smaller picture and fewer colours first, then fewer frames (the time of dropped frames
is added to the ones kept, so the animation runs at the same speed). A GIF that already fits is never touched.
Needs Pillow; without it a big GIF is simply refused with a clear message.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

try:
    from PIL import Image
    HAVE_PILLOW = True
except ImportError:                       # not installed: big GIFs are refused instead of shrunk
    Image = None
    HAVE_PILLOW = False

MAX_PIXELS_PER_FRAME = 9_000_000         # about 4K: refuse absurd canvases before decoding anything
MAX_TOTAL_PIXELS = 80_000_000            # width x height x frames: bounds the time one shrink can take
MAX_FRAMES_KEPT = 200
MAX_SECONDS = 25.0                       # well inside the 60 seconds a CDN waits for the server
FIRST_LONG_SIDE = 800                    # nothing on a phone or projector needs a bigger GIF than this
MIN_SIDE = 64
MIN_FRAME_MS = 20                        # browsers show anything faster than this at 100 ms
SAFETY = 0.9                             # aim a little under the target: the estimate is not exact
RETRIES = 3                              # real attempts at the estimated setting and the next ones up
WINDOW = 7                               # neighbouring frames read at each sample point

# (scale, keep one frame in N, colours): gentle first
STEPS = [(1.0, 1, 256), (1.0, 1, 128), (1.0, 1, 64), (0.85, 1, 128), (1.0, 2, 256), (1.0, 2, 128), (0.85, 2, 128), (0.7, 2, 128),
         (0.7, 2, 64), (0.55, 2, 64), (0.45, 3, 64), (0.35, 3, 32)]


class GifError(Exception):
    """The GIF cannot be shrunk. The message is safe to show to the person who uploaded it."""


def _flat(frame):
    """One decoded frame as plain RGB (transparency is flattened onto white)."""
    rgba = frame.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    return flat


def _fit(img, size: tuple):
    return img.resize(size, Image.Resampling.LANCZOS) if img.size != size else img


def _palette(samples: list, size: tuple, colours: int):
    """One palette for the whole animation, made from a sheet of sample frames."""
    sheet = Image.new("RGB", (size[0], size[1] * len(samples)))
    for k, s in enumerate(samples):
        sheet.paste(_fit(s, size), (0, k * size[1]))
    pal = sheet.quantize(colors=colours, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    del sheet
    return pal


def _map(img, size: tuple, pal):
    return _fit(img, size).quantize(palette=pal, dither=Image.Dither.NONE)


def _encode(frames: list, durations: list) -> bytes:
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=False, disposal=1)
    return out.getvalue()


def _geometry(w: int, h: int, scale: float, base: float) -> tuple:
    s = base * scale
    return (max(MIN_SIDE, round(w * s)), max(MIN_SIDE, round(h * s))) if s < 1 else (w, h)


def _check_clock(started: float):
    if time.monotonic() - started > MAX_SECONDS:
        raise GifError("This GIF took too long to shrink. Use a shorter or smaller one.")


def shrink(data: bytes, target_bytes: int, steps=None, trace: list | None = None) -> dict:
    """Returns {"bytes", "frames", "size", "step"} with len(bytes) <= target_bytes, or raises GifError."""
    if not HAVE_PILLOW:
        raise GifError("This server cannot shrink GIFs. Make the GIF smaller and try again.")
    steps = list(steps or STEPS)
    started = time.monotonic()
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        n = getattr(im, "n_frames", 1)
    except Exception as e:                # a damaged or unreadable file
        raise GifError("That GIF could not be read. Try saving it again.") from e
    if im.format != "GIF":
        raise GifError("That is not a GIF.")
    if w * h > MAX_PIXELS_PER_FRAME or w * h * n > MAX_TOTAL_PIXELS:
        raise GifError("This GIF is too large or long to shrink. Use a shorter or smaller one.")
    base = min(1.0, FIRST_LONG_SIDE / max(w, h))
    work = _geometry(w, h, 1.0, base)

    try:
        # 1. a few short runs of neighbouring frames tell us roughly how big each setting would make the whole GIF: a GIF stores
        #    one full frame and then mostly the changes, so the cost of a full frame and of a following one are measured apart
        windows = []
        for p in sorted({n // 4, n // 2, (3 * n) // 4} if n > 3 else {0}):
            run = []
            for i in range(p, min(n, p + WINDOW)):
                _check_clock(started)
                im.seek(i)
                run.append(_fit(_flat(im), work))                                  # held at working size, never full size
            windows.append(run)
        sheet_samples = [r[0] for r in windows] + [r[min(1, len(r) - 1)] for r in windows]
        chosen = len(steps) - 1
        for index, (scale, every, colours) in enumerate(steps):
            _check_clock(started)
            every = max(every, -(-n // MAX_FRAMES_KEPT))
            size = _geometry(w, h, scale, base)
            pal = _palette(sheet_samples, size, colours)
            full, change = [], []
            for run in windows:
                first = _map(run[0], size, pal)
                f1 = len(_encode([first], [100]))
                full.append(f1)
                if len(run) > every:
                    change.append(max(0, len(_encode([first, _map(run[every], size, pal)], [100, 100])) - f1))
            kept = -(-n // every)
            f_avg = sum(full) / len(full)
            d_avg = sum(change) / len(change) if change else f_avg
            if trace is not None:
                trace.append((index, "estimate", round(f_avg + (kept - 1) * d_avg)))
            if f_avg + (kept - 1) * d_avg <= target_bytes * SAFETY:
                chosen = index
                break
        # 2. the real thing, one frame at a time, at the estimated setting (and the next ones up if the estimate was a little optimistic)
        for index in range(chosen, min(len(steps), chosen + RETRIES)):
            scale, every, colours = steps[index]
            every = max(every, -(-n // MAX_FRAMES_KEPT))
            size = _geometry(w, h, scale, base)
            pal = _palette(sheet_samples, size, colours)
            palettes, durations = [], []
            for i in range(n):
                _check_clock(started)
                im.seek(i)
                d = int(im.info.get("duration", 100) or 100)
                if i % every == 0:
                    palettes.append(_map(_flat(im), size, pal))
                    durations.append(d)
                else:
                    durations[-1] += d                                           # a dropped frame's time goes to the last one kept: the speed stays the same
            durations = [max(MIN_FRAME_MS, d) for d in durations]
            data_out = _encode(palettes, durations)
            del palettes
            if trace is not None:
                trace.append((index, "real", len(data_out)))
            if len(data_out) <= target_bytes:
                return {"bytes": data_out, "frames": len(durations), "size": size, "step": index}
    except GifError:
        raise
    except MemoryError as e:
        raise GifError("This GIF is too large to shrink on this server. Use a shorter or smaller one.") from e
    except Exception as e:                # a damaged file that opened but breaks part-way
        raise GifError("That GIF could not be read. Try saving it again.") from e
    raise GifError("This GIF is still too big after shrinking it. Use a shorter or smaller one.")


def shrink_file(path: Path, target_bytes: int, steps=None) -> dict:
    """Replace the file with a shrunk copy. Returns {"from", "to", "frames", "size"}. The original is left alone if anything fails."""
    original = path.read_bytes()
    result = shrink(original, target_bytes, steps)
    tmp = path.with_suffix(".shrinking")
    try:
        tmp.write_bytes(result["bytes"])
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return {"from": len(original), "to": len(result["bytes"]), "frames": result["frames"], "size": result["size"]}
