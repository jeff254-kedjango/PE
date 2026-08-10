"""Generic commerce-listing media upload — POST /api/v1/media/trade.

Load-bearing properties under test:
  1. Happy path: images + an optional short video → /uploads/trade/... URLs, files written.
  2. The 250 MB cap is enforced WHILE STREAMING (we lower the cap via monkeypatch to keep the test
     fast) and an over-cap upload returns 413 AND leaves NO partial file on disk — the cleanup
     guarantee that keeps a rejected upload from filling the disk.
  3. A bad content-type → 400, and no file is written.
  4. Unauthenticated → 401 (the route is any-USER but still requires a valid session token).

The route persists no DB row, so these tests only need an authenticated user override + a temp
UPLOAD_DIR so writes don't touch the repo.
"""
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from PE.weespas.main import app
from PE.weespas.models.user import User, UserRole
from PE.weespas.routers import media as media_router
from PE.weespas.services.auth_service import get_current_user

URL = "/api/v1/media/trade"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect every storage dir at the trade subtree under tmp so the test never writes into the repo.
    timg = tmp_path / "trade" / "images"
    tvid = tmp_path / "trade" / "videos"
    timg.mkdir(parents=True, exist_ok=True)
    tvid.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(media_router, "TRADE_IMAGE_DIR", timg)
    monkeypatch.setattr(media_router, "TRADE_VIDEO_DIR", tvid)

    user = User(
        id=str(uuid.uuid4()), name="Seller", email="s@t.co",
        phone="+254700000000", hashed_password="x", role=UserRole.USER,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app, raise_server_exceptions=False), timg, tvid
    app.dependency_overrides.pop(get_current_user, None)


def _files_on_disk(*dirs) -> int:
    return sum(len(list(d.iterdir())) for d in dirs)


def test_upload_images_and_video_happy_path(client):
    c, timg, tvid = client
    resp = c.post(
        URL,
        files=[
            ("images", ("a.jpg", b"\xff\xd8imagedata", "image/jpeg")),
            ("images", ("b.png", b"pngdata", "image/png")),
            ("video", ("clip.mp4", b"smallvideobytes", "video/mp4")),
        ],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["uploaded"] == 3
    assert len(body["images"]) == 2
    assert all(i["url"].startswith("/uploads/trade/images/") for i in body["images"])
    # thumbnail == url (transcode deferred)
    assert all(i["thumbnail_url"] == i["url"] for i in body["images"])
    assert body["video"]["url"].startswith("/uploads/trade/videos/")
    assert _files_on_disk(timg) == 2 and _files_on_disk(tvid) == 1


def test_video_over_cap_413_and_no_partial_file(client, monkeypatch):
    c, timg, tvid = client
    # Lower the cap to 8 bytes so a tiny payload trips it (the real cap is 250 MB; we don't want to
    # push 250 MB through a test). The point is the WHILE-streaming enforcement + partial cleanup.
    monkeypatch.setattr(media_router, "MAX_TRADE_VIDEO_SIZE", 8)
    resp = c.post(
        URL,
        files=[("video", ("big.mp4", b"way-too-many-bytes-here", "video/mp4"))],
    )
    assert resp.status_code == 413, resp.text
    # The partial file MUST have been cleaned up — a rejected upload leaves nothing behind.
    assert _files_on_disk(tvid) == 0


def test_bad_image_type_400_and_nothing_written(client):
    c, timg, tvid = client
    resp = c.post(
        URL,
        files=[("images", ("evil.gif", b"gifdata", "image/gif"))],
    )
    assert resp.status_code == 400
    assert _files_on_disk(timg) == 0


def test_empty_request_400(client):
    c, _, _ = client
    # No images and no video at all — the route has nothing to store. A multipart POST with no file
    # parts leaves images=[] / video=None (the field defaults), which the guard rejects as 400.
    resp = c.post(URL, data={"_": "x"})
    assert resp.status_code == 400


def test_requires_authentication(client):
    c, _, _ = client
    # Drop the auth override → the real get_current_user runs and rejects the missing token.
    app.dependency_overrides.pop(get_current_user, None)
    resp = c.post(URL, files=[("images", ("a.jpg", b"x", "image/jpeg"))])
    assert resp.status_code in (401, 403)
