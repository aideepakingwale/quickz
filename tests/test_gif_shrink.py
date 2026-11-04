"""Big animated GIFs are shrunk by the server so they fit the picture limit and still animate."""

import io
import random

import pytest
from fastapi.testclient import TestClient

from quickz import gifshrink
from tests.conftest import CSRF, setup_admin

PIL = pytest.importorskip("PIL.Image")
from PIL import Image, ImageDraw  # noqa: E402


def make_gif(frames=24, w=480, h=360, noise=20000, seed=1) -> bytes:
    rnd = random.Random(seed)
    imgs = []
    for i in range(frames):
        im = Image.new("RGB", (w, h), ((20 + i * 3) % 200, 60, 140))
        d = ImageDraw.Draw(im)
        for k in range(6):
            x, y = (i * 17 + k * 53) % w, (i * 11 + k * 37) % h
            d.ellipse([x, y, x + 50, y + 50], fill=(255 - k * 25, 80 + k * 20, (i * 9) % 255))
        px = im.load()
        for _ in range(noise):
            px[rnd.randrange(w), rnd.randrange(h)] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        imgs.append(im)
    out = io.BytesIO()
    imgs[0].save(out, format="GIF", save_all=True, append_images=imgs[1:], duration=80, loop=0, optimize=False)
    return out.getvalue()


@pytest.fixture
def admin(app):
    c = TestClient(app, headers=CSRF)
    setup_admin(c)
    return c


@pytest.fixture(scope="module")
def big_gif():
    data = make_gif(frames=30, w=640, h=480, noise=60000)
    assert len(data) > 2 * 1024 * 1024
    return data


def info(data: bytes):
    im = Image.open(io.BytesIO(data))
    return {"format": im.format, "frames": im.n_frames, "animated": getattr(im, "is_animated", False), "size": im.size, "loop": im.info.get("loop")}


def test_a_gif_that_already_fits_is_stored_exactly_as_uploaded(admin, app):
    small = make_gif(frames=8, w=200, h=150, noise=500)
    assert len(small) < 2 * 1024 * 1024
    r = admin.post("/api/uploads", content=small)
    assert r.status_code == 200 and "shrunk" not in r.json()
    assert admin.get(r.json()["url"]).content == small


def test_a_big_animated_gif_is_shrunk_to_fit_and_still_animates(admin, big_gif):
    r = admin.post("/api/uploads", content=big_gif)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["shrunk"]["from"] == len(big_gif) and d["shrunk"]["to"] <= 2 * 1024 * 1024 < d["shrunk"]["from"]
    stored = admin.get(d["url"]).content
    assert len(stored) == d["shrunk"]["to"] and stored[:6] in (b"GIF87a", b"GIF89a")
    i = info(stored)
    assert i["format"] == "GIF" and i["animated"] and i["frames"] >= 8 and i["loop"] == 0
    assert admin.get(d["url"]).headers["content-type"] == "image/gif"


def test_shrinking_keeps_the_speed_when_frames_are_dropped(big_gif):
    result = gifshrink.shrink(big_gif, 700_000)
    im = Image.open(io.BytesIO(result["bytes"]))
    total = 0
    for i in range(im.n_frames):
        im.seek(i)
        total += im.info.get("duration", 0)
    assert result["frames"] < 30 and abs(total - 30 * 80) <= 80 * 3        # about the same running time as the original 30 x 80 ms


def test_the_limit_follows_the_admin_setting(admin, big_gif):
    admin.put("/api/admin/settings", json={"max_image_mb": 1})
    r = admin.post("/api/uploads", content=big_gif)
    assert r.status_code == 200 and r.json()["shrunk"]["to"] <= 1024 * 1024
    admin.put("/api/admin/settings", json={"max_image_mb": 5})
    r = admin.post("/api/uploads", content=big_gif)
    assert r.status_code == 200 and "shrunk" not in r.json()                 # now it fits as it is


def test_a_gif_over_the_gif_limit_is_refused_before_any_work(admin, app):
    admin.put("/api/admin/settings", json={"max_gif_mb": 3})
    r = admin.post("/api/uploads", content=b"GIF89a" + b"\x00" * (3 * 1024 * 1024 + 500))
    assert r.status_code == 413 and "max 3 MB" in r.text


def test_other_pictures_are_still_capped_without_any_shrinking(admin):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2100 * 1024)
    r = admin.post("/api/uploads", content=png)
    assert r.status_code == 413 and "Image too large (max 2 MB)" in r.text


def test_a_gif_that_cannot_be_shrunk_enough_is_refused_and_leaves_no_file(admin, app, monkeypatch, big_gif):
    monkeypatch.setattr(gifshrink, "STEPS", [(1.0, 1, 256)])               # a shrinker with no room to improve
    before = {p.name for p in app.state.settings.uploads_dir.iterdir()}
    r = admin.post("/api/uploads", content=make_gif(frames=30, w=640, h=480, noise=60000, seed=2))
    assert r.status_code == 413 and "still too big" in r.text and "at most 2 MB" in r.text
    assert {p.name for p in app.state.settings.uploads_dir.iterdir()} == before


def test_a_damaged_gif_is_refused_clearly_and_leaves_no_file(admin, app):
    junk = b"GIF89a" + bytes([7]) * (3 * 1024 * 1024)
    before = {p.name for p in app.state.settings.uploads_dir.iterdir()}
    r = admin.post("/api/uploads", content=junk)
    assert r.status_code == 413 and "could not be read" in r.text
    assert {p.name for p in app.state.settings.uploads_dir.iterdir()} == before


def test_absurdly_large_canvases_and_long_gifs_are_refused_without_decoding(monkeypatch):
    huge = Image.new("P", (6000, 6000))
    buf = io.BytesIO()
    huge.save(buf, format="GIF")
    with pytest.raises(gifshrink.GifError, match="too large or long"):
        gifshrink.shrink(buf.getvalue(), 1000)
    monkeypatch.setattr(gifshrink, "MAX_TOTAL_PIXELS", 100_000)           # 12 frames of 200 x 150 is 360,000
    with pytest.raises(gifshrink.GifError, match="too large or long"):
        gifshrink.shrink(make_gif(frames=12, w=200, h=150, noise=10), 1000)


def test_transparency_is_flattened_onto_white_and_a_static_gif_still_works():
    im = Image.new("P", (300, 200), 0)                                      # palette entry 0 is the transparent one, entry 1 is red
    im.putpalette([0, 0, 0, 200, 30, 30])
    ImageDraw.Draw(im).ellipse([20, 20, 200, 160], fill=1)
    buf = io.BytesIO()
    im.save(buf, format="GIF", transparency=0)
    result = gifshrink.shrink(buf.getvalue(), 10_000_000)
    out = Image.open(io.BytesIO(result["bytes"])).convert("RGB")
    assert out.getpixel((2, 2)) == (255, 255, 255) and out.getpixel((100, 90))[0] > 150 and out.getpixel((100, 90))[1] < 100
    assert result["frames"] == 1
