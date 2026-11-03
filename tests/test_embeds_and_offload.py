"""YouTube/Vimeo embeds, pictures linked from allowed hosts, background preloading, and serving uploads from the bucket."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from conftest import CSRF, SETUP_TOKEN, setup_admin
from quickz import questions as qmod
from quickz.config import Settings
from quickz.db import Database
from quickz.engine import GameManager
from quickz.questions import ValidationError, parse_embed_link, validate_question, validate_quiz
from quickz.server import create_app
from test_cloud import PNG, FakeS3, wait_for  # noqa: F401  (s3 fixture below)

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def s3():
    f = FakeS3()
    yield f
    f.close()


def mcq(**kw):
    return {"type": "mcq", "text": "Q?", "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct": ["a"], "rich": True, **kw}


# ------------------------------------------------------------------ embeds
@pytest.mark.parametrize("link,provider,vid,start", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m30s", "youtube", "dQw4w9WgXcQ", 90),
    ("https://youtu.be/dQw4w9WgXcQ?t=42", "youtube", "dQw4w9WgXcQ", 42),
    ("youtube.com/shorts/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", 0),
    ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", 0),
    ("https://vimeo.com/76979871", "vimeo", "76979871", 0),
    ("https://player.vimeo.com/video/76979871?h=abcdef1234", "vimeo", "76979871", 0),
])
def test_embed_links_are_understood(link, provider, vid, start):
    e = parse_embed_link(link)
    assert (e["provider"], e["id"], e["start"]) == (provider, vid, start)


@pytest.mark.parametrize("bad", [
    "https://evil.example/watch?v=dQw4w9WgXcQ", "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ", "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
    "javascript:alert(1)", "https://youtube.com/watch?v=short", "https://youtube.com/", "https://vimeo.com/abc", "", "ftp://youtube.com/watch?v=dQw4w9WgXcQ"])
def test_anything_that_is_not_a_real_video_link_is_refused(bad):
    with pytest.raises(ValidationError):
        parse_embed_link(bad)


def test_embed_media_is_normalised_and_cannot_smuggle_an_address():
    q = validate_question(mcq(media={"kind": "embed", "link": YT, "url": "https://evil.example/x", "start": "20", "end": 50, "cover": True, "where": "both", "timer_after": True}))
    m = q["media"]
    assert (m["provider"], m["id"], m["start"], m["end"], m["cover"], m["where"], m["timer_after"]) == ("youtube", "dQw4w9WgXcQ", 20, 50, True, "both", True)
    assert m["url"] == YT                                         # rebuilt from the validated ID, never the pasted text
    with pytest.raises(ValidationError):
        validate_question(mcq(media={"kind": "embed", "link": YT, "start": 60, "end": 30, "where": "screen"}))
    assert validate_question({"type": "buzzer", "text": "x", "media": {"kind": "embed", "link": YT, "timer_after": True}})["media"]["timer_after"] is False
    with pytest.raises(ValidationError):
        validate_question(mcq(media={"kind": "video", "url": "https://youtube.com/x.mp4"}))


def test_players_only_learn_which_video_when_their_device_is_meant_to_play_it():
    async def go():
        db = Database(":memory:")
        qid = db.create_quiz(validate_quiz({"title": "T", "questions": [mcq(media={"kind": "embed", "link": YT, "where": "screen", "cover": True})]}))
        g = GameManager(db).create(qid, {})
        p = g.join("P")
        await g.next()
        ranks = g._ranks()
        assert g.view_screen(ranks)["q"]["media"]["id"] == "dQw4w9WgXcQ" and g.view_screen(ranks)["q"]["media"]["cover"] is True
        pm = g.view_player(p, ranks)["q"]["media"]
        assert pm["url"] is None and "id" not in pm
        await g.end_question()
        assert g.view_screen(g._ranks())["q"]["media"]["url"] is None     # the clip stops at the reveal

    run(go())


# ------------------------------------------------------------------ pictures linked from the web
def test_pictures_can_be_linked_from_allowed_hosts_only():
    ok = validate_question(mcq(image="https://media.giphy.com/media/abc/giphy.gif"))
    assert ok["image"].startswith("https://media.giphy.com/")
    assert validate_question(mcq(options=[{"id": "a", "text": "", "image": "https://upload.wikimedia.org/x.png"}, {"id": "b", "text": "B"}]))["options"][0]["image"].endswith("x.png")
    for bad in ("https://evil.example/x.png", "https://giphy.com.evil.example/x.gif", "http://media.giphy.com/x.gif", "https://user:pw@media.giphy.com/x.gif", "https://media.giphy.com/a b.gif"):
        with pytest.raises(ValidationError):
            validate_question(mcq(image=bad))


def test_an_instance_can_allow_more_picture_hosts(tmp_path, monkeypatch):
    monkeypatch.setattr(qmod, "IMAGE_HOSTS", set(qmod.IMAGE_HOSTS))
    create_app(Settings(data_dir=tmp_path, public_url="http://x", setup_token=SETUP_TOKEN, image_hosts=("images.myschool.example",)), cloud=False)
    assert validate_question(mcq(image="https://images.myschool.example/logo.png"))["image"]


def test_embed_parse_endpoint(client):
    r = client.post("/api/embed/parse", json={"link": "https://youtu.be/dQw4w9WgXcQ?t=5"})
    assert r.status_code == 200 and r.json()["start"] == 5 and r.json()["provider"] == "youtube"
    assert client.post("/api/embed/parse", json={"link": "https://evil.example/x"}).status_code == 400


# ------------------------------------------------------------------ background preloading
def test_the_next_questions_pictures_are_offered_for_preloading_but_puzzle_pictures_are_not():
    async def go():
        db = Database(":memory:")
        pic = lambda n: f"/static/samples/{n}.svg"
        qs = [mcq(text="one"),
              mcq(text="two", image=pic("rocket"), options=[{"id": "a", "text": "", "image": pic("flag-italy")}, {"id": "b", "text": "", "image": pic("flag-france")}], explain_image=pic("map")),
              {"type": "text", "text": "guess", "answers": ["x"], "image": pic("rocket"), "image_effect": "blur", "rich": True}]
        g = GameManager(db).create(db.create_quiz(validate_quiz({"title": "T", "questions": qs})), {})
        p = g.join("P")
        assert g.view_player(p, g._ranks())["preload"] == []                      # the first question has no pictures
        await g.next()                                                            # on q1: phones fetch q2's pictures quietly
        assert g.view_player(p, g._ranks())["preload"] == [pic("rocket"), pic("flag-italy"), pic("flag-france")]
        await g.end_question(); await g.next()                                    # on q2: its explanation picture; q3 is a puzzle picture
        assert g.view_player(p, g._ranks())["preload"] == [pic("map")]
        await g.end_question()
        assert g.view_screen(g._ranks())["preload"] == []                         # nothing hidden is ever previewed
        await g.next(); await g.end_question(); await g.finish()
        assert "preload" not in g.view_player(p, g._ranks())

    run(go())


# ------------------------------------------------------------------ serving uploads from the bucket
def build(tmp_path, cloud, **kw):
    return create_app(Settings(data_dir=tmp_path, public_url="http://x", setup_token=SETUP_TOKEN, **kw), cloud=cloud)


def test_uploads_redirect_to_the_public_bucket_once_they_are_there(tmp_path, s3):
    cloud = s3.cloud()
    app = build(tmp_path, cloud, public_bucket_url="https://pub.example.dev/")
    with TestClient(app, headers=CSRF) as c:
        setup_admin(c)
        url = c.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"}).json()["url"]
        name = url.rsplit("/", 1)[1]
        assert wait_for(lambda: f"/bucket/qz/uploads/{name}" in s3.objects)
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == f"https://pub.example.dev/qz/uploads/{name}"
        assert "max-age" in r.headers["cache-control"]


def test_uploads_are_served_locally_until_the_bucket_has_them_and_when_no_bucket_url_is_set(tmp_path, s3):
    cloud = s3.cloud()
    with TestClient(build(tmp_path / "a", cloud), headers=CSRF) as c:         # no public URL configured: never redirect
        setup_admin(c)
        url = c.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"}).json()["url"]
        assert c.get(url, follow_redirects=False).status_code == 200
    with TestClient(build(tmp_path / "b", s3.cloud(), public_bucket_url="https://pub.example.dev"), headers=CSRF) as c:
        setup_admin(c)
        (tmp_path / "b" / "uploads" / "0123456789abcdef.png").write_bytes(PNG)       # on disk, but never pushed to the bucket
        r = c.get("/uploads/0123456789abcdef.png", follow_redirects=False)
        assert r.status_code == 200 and r.content == PNG


def test_bucket_objects_get_real_content_types():
    from quickz.cloud import upload_content_type
    assert upload_content_type("a.mp3") == "audio/mpeg" and upload_content_type("a.MP4") == "video/mp4"
    assert upload_content_type("a.png") == "image/png" and upload_content_type("a.xyz") == "application/octet-stream"
