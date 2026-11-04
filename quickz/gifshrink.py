"""Shrink a too-big animated GIF so it fits the picture limit, keeping it animated.

A browser cannot do this (drawing a GIF on a canvas keeps only one frame), so the server does it. It tries gentler settings
first and only goes further if the result is still too big: a smaller picture, fewer colours, then fewer frames (the time of
the dropped frames is added to the ones kept, so the animation runs at the same speed). A GIF that already fits is never
touched. Needs Pillow; without it a big GIF is simply refused with a clear message.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

try:
    from PIL import Image, ImageSequence
    HAVE_PILLOW = True
except ImportError:                       # not installed: big GIFs are refused instead of shrunk
    Image = ImageSequence = None
    HAVE_PILLOW = False

MAX_PIXELS_PER_FRAME = 25_000_000        # refuse absurd canvases before decoding anything
MAX_TOTAL_PIXELS = 150_000_000           # width x height x frames: bounds the memory and time a shrink can use
MAX_FRAMES_KEPT = 300
MAX_SECONDS = 12.0
FIRST_LONG_SIDE = 800                    # nothing on a phone or projector needs a bigger GIF than this
MIN_SIDE = 64
MIN_FRAME_MS = 20                        # browsers show anything faster than this at 100 ms

# (scale, keep one frame in N, colours): gentler first
STEPS = [(1.0, 1, 256), (0.85, 1, 128), (0.7, 1, 96), (0.7, 2, 96), (0.55, 2, 64), (0.45, 2, 48), (0.4, 3, 32), (0.3, 3, 32)]


class GifError(Exception):
    """The GIF cannot be shrunk. The message is safe to show to the person who uploaded it."""


def _encode(frames: list, durations: list, colours: int, size: tuple) -> bytes:
    palettes = [f.convert("RGB").quantize(colors=colours, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE) for f in frames]
    out = io.BytesIO()
    palettes[0].save(out, format="GIF", save_all=True, append_images=palettes[1:], duration=durations, loop=0, optimize=True, disposal=1)
    return out.getvalue()


def shrink(data: bytes, target_bytes: int, steps=None) -> dict:
    """Returns {"bytes", "frames", "size", "step"} with len(bytes) <= target_bytes, or raises GifError."""
    if not HAVE_PILLOW:
        raise GifError("This server cannot shrink GIFs. Make the GIF smaller and try again.")
    started = time.monotonic()
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:                # a damaged or unreadable file
        raise GifError("That GIF could not be read. Try saving it again.") from e
    if im.format != "GIF":
        raise GifError("That is not a GIF.")
    w, h = im.size
    frames_total = getattr(im, "n_frames", 1)
    if w * h > MAX_PIXELS_PER_FRAME or w * h * frames_total > MAX_TOTAL_PIXELS:
        raise GifError("This GIF is too large or long to shrink. Use a shorter or smaller one.")
    # one decoding pass: full frames with their durations
    raw, times = [], []
    for frame in ImageSequence.Iterator(im):
        if time.monotonic() - started > MAX_SECONDS:
            raise GifError("This GIF took too long to shrink. Use a shorter or smaller one.")
        rgba = frame.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))                    # transparency is flattened onto white
        flat.paste(rgba, mask=rgba.split()[3])
        raw.append(flat)
        times.append(int(frame.info.get("duration", im.info.get("duration", 100)) or 100))
    long_side = max(w, h)
    base = min(1.0, FIRST_LONG_SIDE / long_side)
    for index, (scale, every, colours) in enumerate(steps or STEPS):
        if time.monotonic() - started > MAX_SECONDS:
            break
        every = max(every, -(-len(raw) // MAX_FRAMES_KEPT))                       # never keep more than MAX_FRAMES_KEPT
        s = base * scale
        size = (max(MIN_SIDE, round(w * s)), max(MIN_SIDE, round(h * s))) if s < 1 else (w, h)
        picked, durations = [], []
        for i in range(0, len(raw), every):
            frame = raw[i]
            picked.append(frame.resize(size, Image.Resampling.LANCZOS) if size != (w, h) else frame)
            durations.append(max(MIN_FRAME_MS, sum(times[i:i + every])))          # the dropped frames' time is added, so the speed stays the same
        data_out = _encode(picked, durations, colours, size)
        if len(data_out) <= target_bytes:
            return {"bytes": data_out, "frames": len(picked), "size": size, "step": index}
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
