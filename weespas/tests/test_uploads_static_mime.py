"""Static /uploads Content-Type correctness.

WebP is the format weespas PREFERS on disk: routers/media.py transcodes uploads to it, and both
upload paths (media.py, me.py avatars) explicitly accept image/webp. But StaticFiles derives
Content-Type from ``mimetypes.guess_type``, and CPython's built-in table has no ``.webp`` entry
on this interpreter — so every WebP was served as ``text/plain; charset=utf-8`` until main.py
registered the type.

Why this is worth a test rather than a one-line fix left to trust:

  * It renders correctly in a browser TODAY only because nothing sets
    ``X-Content-Type-Options: nosniff`` — browsers fall back to sniffing magic bytes. So the bug
    is INVISIBLE in manual testing, and would surface only when someone adds nosniff (a routine
    hardening step) and every image on the site breaks at once, far from any image-related change.
  * ``mimetypes`` is process-global and initialised from the OS mime table, so the correct type
    can appear on one machine and vanish on another. Asserting it pins behaviour to the explicit
    registration in main.py instead of to whatever /etc/mime.types happens to contain.

The mount is rooted at the process CWD (``Path("uploads")``), so these tests write their fixture
files into the real uploads tree under a uniquely-named subdirectory and remove it afterwards —
that is the only way to exercise the actual mount rather than a re-mounted copy of it.
"""
import mimetypes
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from PE.weespas.main import app

# Minimal valid files. Content only has to be enough that a browser COULD sniff it; the
# assertions are about the declared header, not the payload.
_WEBP = (
    b"RIFF" + (26).to_bytes(4, "little") + b"WEBPVP8L"
    + (10).to_bytes(4, "little") + b"\x2f\x00\x00\x00\x00\x88\x88\x08\x00\x00"
)
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def served(tmp_path_factory):
    """Write fixture files into the REAL mounted uploads tree, then clean up.

    Returns a callable: (filename, bytes) -> URL path served by the mount.
    """
    # Must match main.py's mount root exactly (CWD-relative), or we would be testing a
    # directory nothing serves.
    root = Path("uploads") / f"_mimetest-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)

    def _put(name: str, blob: bytes) -> str:
        (root / name).write_bytes(blob)
        return f"/{root.as_posix()}/{name}"

    yield _put
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def client():
    return TestClient(app)


class TestUploadsMimeTypes:
    def test_webp_is_served_as_image_not_text(self, client, served):
        """The regression. Before main.py registered the type this returned text/plain, which
        only worked because browsers sniff — and would break the moment nosniff was added."""
        url = served("avatar.webp", _WEBP)
        r = client.get(url)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/webp"
        # Explicitly pin the old wrong behaviour so a future mimetypes/StaticFiles change that
        # reintroduces a text fallback fails here rather than silently in a browser.
        assert "text/plain" not in r.headers["content-type"]

    def test_webp_registration_is_process_wide(self):
        """Importing PE.weespas.main must be sufficient — the mount is not the only consumer of
        guess_type (routers build URLs, tooling inspects files), so the registration is asserted
        directly rather than only through a response header."""
        assert mimetypes.guess_type("x.webp")[0] == "image/webp"

    def test_other_stored_formats_keep_their_correct_types(self, client, served):
        """Guards the fix's blast radius: adding a .webp entry must not disturb the extensions
        that already resolved correctly. png/jpg/mp4 are the other three formats actually present
        in the uploads tree."""
        assert client.get(served("shot.png", _PNG)).headers["content-type"] == "image/png"
        # jpg/mp4 need no real payload — StaticFiles types by extension, and an empty file still
        # exercises the same lookup the browser depends on.
        assert client.get(served("shot.jpg", b"\xff\xd8\xff\xdb")).headers["content-type"] == "image/jpeg"
        assert client.get(served("clip.mp4", b"\x00\x00\x00\x18ftyp")).headers["content-type"] == "video/mp4"

    def test_missing_file_is_404_not_a_typed_empty_body(self, client):
        """A dangling avatar path (row points at a deleted file) must 404 so the frontend's
        onError fallback fires. If it ever returned 200 with an image type, ViewingCard would
        render a permanently broken image instead of the monogram."""
        r = client.get("/uploads/avatars/definitely-not-here-{}.webp".format(uuid.uuid4().hex))
        assert r.status_code == 404
