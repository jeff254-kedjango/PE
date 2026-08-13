"""Avatar downscale-on-transcode — services/image_processing.py.

Avatars were stored at whatever the camera produced (measured: up to 4160x6240, 787 KB mean) and
served into a 40px circle on the seller console's Viewing Card. The WebP transcode worker already
decodes every avatar, so bounding the longest edge there is free — and keeps POST /me/avatar
sub-50ms, which is why the resize does NOT live in the endpoint.

What these tests defend:

  1. Avatars come out bounded to AVATAR_MAX_EDGE, with aspect ratio preserved.
  2. Property images are NOT affected. `_optimize_one` is shared, and property photos are viewed
     full-screen in a lightbox — silently shrinking them would be a visible regression in a
     different feature. The `max_edge=None` default is load-bearing, so it is asserted.
  3. A small avatar is never UPSCALED. `thumbnail()` no-ops within bounds; `resize()` would have
     blown a 64px upload up to 256px and made it blurry AND larger.
  4. The full-resolution source file SURVIVES. image_processing.py keeps it deliberately so
     already-cached client URLs don't 404 (see its docstring). A resize must not change that
     contract — this is the test that would catch someone "tidying up" by deleting the source.

The Celery task body is called directly (`.run`-style plain invocation) rather than dispatched:
these assertions are about the image maths and the DB write, not the broker.
"""
import pytest
from PIL import Image

from PE.weespas.services import image_processing as ip
from PE.weespas.services.image_processing import AVATAR_MAX_EDGE, _optimize_one


def _make_image(path, size, fmt="JPEG"):
    """Write a real image of exact dimensions. Content is a gradient rather than flat colour so
    WebP can't compress it to near-nothing and mask a size regression."""
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for x in range(w):
        for y in range(h):
            px[x, y] = ((x * 7) % 256, (y * 11) % 256, ((x + y) * 3) % 256)
    img.save(path, fmt)
    return path


class TestOptimizeOneResize:
    def test_bounds_the_longest_edge_and_keeps_aspect_ratio(self, tmp_path):
        # Portrait, like the real phone-camera avatars that motivated this (3:2 tall).
        src = _make_image(tmp_path / "big.jpg", (600, 900))
        out = _optimize_one(str(src), max_edge=256)
        assert out is not None
        with Image.open(out) as im:
            assert max(im.size) == 256, im.size
            # 600x900 -> 171x256 (600 * 256/900 = 170.67, rounded up). Aspect ratio held to
            # within a pixel of the 0.667 source ratio.
            assert im.size == (171, 256), im.size
            assert abs(im.size[0] / im.size[1] - 600 / 900) < 0.01

    def test_landscape_bounds_the_width(self, tmp_path):
        """The longest edge is bounded whichever axis it is on — a wide image must not keep a
        4000px width just because its height is small."""
        src = _make_image(tmp_path / "wide.jpg", (900, 300))
        out = _optimize_one(str(src), max_edge=256)
        with Image.open(out) as im:
            assert im.size == (256, 85), im.size

    def test_does_not_upscale_a_small_image(self, tmp_path):
        """thumbnail() no-ops within bounds. resize() would have produced a blurry 256px file
        that is LARGER than the 64px original — worse on both axes we care about."""
        src = _make_image(tmp_path / "small.jpg", (64, 64))
        out = _optimize_one(str(src), max_edge=256)
        with Image.open(out) as im:
            assert im.size == (64, 64), im.size

    def test_default_does_not_resize_at_all(self, tmp_path):
        """Load-bearing default. `_optimize_one` is shared with property images, which are viewed
        full-screen in a lightbox; an accidental bound here would degrade that feature silently."""
        src = _make_image(tmp_path / "prop.jpg", (1200, 800))
        out = _optimize_one(str(src))          # no max_edge
        with Image.open(out) as im:
            assert im.size == (1200, 800), im.size

    def test_downscale_actually_shrinks_the_file(self, tmp_path):
        """The point of the change is bytes on the wire, so assert bytes — not just dimensions.
        A dimension-only test would pass even if quality were cranked to 100 and the file grew."""
        src = _make_image(tmp_path / "heavy.jpg", (2000, 3000))
        big = _optimize_one(str(src))
        big_bytes = big.stat().st_size
        big.unlink()                            # same output path; take the measurement first
        small = _optimize_one(str(src), max_edge=AVATAR_MAX_EDGE)
        assert small.stat().st_size < big_bytes / 5, (small.stat().st_size, big_bytes)

    def test_missing_file_returns_none(self, tmp_path):
        assert _optimize_one(str(tmp_path / "nope.jpg"), max_edge=256) is None


class TestProcessAvatarImage:
    def test_transcode_bounds_the_avatar_and_keeps_the_source(self, tmp_path, monkeypatch):
        """End-to-end through the Celery task body, with the DB stubbed out.

        Two properties in one test because they are one contract: the served WebP is bounded AND
        the full-resolution source is still on disk. image_processing.py keeps the source so
        client URLs cached before the transcode don't 404; a future "cleanup" that deletes it
        would reintroduce the blank-avatar window this test forbids.
        """
        src = _make_image(tmp_path / "u1-abcd1234.jpg", (4160, 6240))
        source_bytes_before = src.stat().st_size

        class _FakeQuery:
            def filter(self, *a, **k):
                return self

            def first(self):
                return user

            def update(self, *a, **k):
                return 1

        class _FakeDB:
            def query(self, *a, **k):
                return _FakeQuery()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        class _User:
            id = "u1"
            avatar = None
            agent_id = None

        user = _User()
        monkeypatch.setattr(ip, "SessionLocal", lambda: _FakeDB())

        assert ip.process_avatar_image(str(src), "u1") == "ok"

        # The row now points at the WebP variant...
        assert user.avatar == f"/uploads/avatars/{src.stem}.webp"
        # ...which is bounded to the avatar ceiling.
        with Image.open(src.with_suffix(".webp")) as im:
            assert max(im.size) == AVATAR_MAX_EDGE, im.size
        # ...and the untouched full-resolution source is still serveable.
        assert src.exists(), "source deleted — cached client URLs would 404"
        assert src.stat().st_size == source_bytes_before

    def test_ceiling_covers_the_largest_rendered_avatar(self):
        """Guards the constant against being lowered below what the UI actually renders. The
        largest avatar in the frontend is 110px, so 2x retina needs >=220."""
        assert AVATAR_MAX_EDGE >= 220
