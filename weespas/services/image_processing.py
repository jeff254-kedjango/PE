import subprocess
from pathlib import Path
from PE.weespas.core.celery_app import celery_app

from PIL import Image

from PE.weespas.core.database import SessionLocal
from PE.weespas.models.property import Agent, PropertyImage, PropertyVideo
from PE.weespas.models.user import User


def _optimize_one(file_path: str, *, max_edge: int | None = None) -> Path | None:
    """Convert a single image to WebP. Returns the output path, or None on failure.

    ``max_edge`` bounds the LONGEST side, preserving aspect ratio; ``None`` (the default) keeps
    the source dimensions. It defaults to None because property images are viewed full-screen in
    a lightbox and must stay large — only callers that know their display ceiling pass a value.

    ``thumbnail()`` (not ``resize()``) is deliberate: it is a no-op when the image is already
    within bounds, so a small upload is never UPscaled into a blurry, larger file. It also
    preserves aspect ratio without us computing the second edge.
    """
    path = Path(file_path)
    if not path.exists():
        return None
    output_path = path.with_suffix(".webp")
    with Image.open(path) as img:
        img = img.convert("RGB")
        if max_edge is not None:
            # LANCZOS is the right filter for large downscale ratios (a 4160px source to 256px is
            # ~16x); the cheaper default would alias badly on facial detail at avatar sizes.
            img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        img.save(output_path, "WEBP", quality=80)
    return output_path


@celery_app.task(name="process_property_image")
def process_property_image(file_path: str, image_id: int):
    try:
        output_path = _optimize_one(file_path)
        if output_path is None:
            return "File not found"

        db = SessionLocal()
        try:
            image_record = db.query(PropertyImage).filter(PropertyImage.id == image_id).first()
            if image_record:
                image_record.thumbnail_url = f"/uploads/images/{output_path.name}"
                db.commit()
                return f"Success: Image {image_id} optimized to WebP"
        finally:
            db.close()

    except Exception as e:
        return f"Image processing failed: {str(e)}"


@celery_app.task(name="process_property_images_batch")
def process_property_images_batch(items: list[dict]):
    """
    Process a batch of images in a single task with one DB session.
    `items` is a list of {"file_path": str, "image_id": int}.
    """
    if not items:
        return "No items"

    results: dict[int, str] = {}
    db = SessionLocal()
    try:
        ids = [it["image_id"] for it in items]
        records = {
            r.id: r
            for r in db.query(PropertyImage).filter(PropertyImage.id.in_(ids)).all()
        }

        for it in items:
            image_id = it["image_id"]
            file_path = it["file_path"]
            try:
                output_path = _optimize_one(file_path)
                if output_path is None:
                    results[image_id] = "missing"
                    continue
                record = records.get(image_id)
                if record:
                    record.thumbnail_url = f"/uploads/images/{output_path.name}"
                    results[image_id] = "ok"
                else:
                    results[image_id] = "no_record"
            except Exception as e:
                results[image_id] = f"error: {e}"

        db.commit()
    finally:
        db.close()

    return results


@celery_app.task(name="process_property_video")
def process_property_video(file_path: str, video_id: int): # Added video_id here
    path = Path(file_path)
    if not path.exists():
        return "File not found"

    # Define the thumbnail path (e.g., video_name.jpg)
    thumbnail_path = path.with_suffix(".jpg")

    try:
        # Run FFmpeg to extract the frame
        subprocess.run([
            'ffmpeg', '-y', '-ss', '00:00:01', '-i', str(path),
            '-vframes', '1', '-q:v', '2', str(thumbnail_path)
        ], check=True, capture_output=True) # Added capture_output for cleaner logs
        
        # Update the Database
        db = SessionLocal()
        try:
            # Finding the record to link the new thumbnail
            video = db.query(PropertyVideo).filter(PropertyVideo.id == video_id).first()
            if video:
                # We store the relative URL so the frontend can find it
                video.thumbnail_url = f"/uploads/videos/{thumbnail_path.name}"
                db.commit()
                return f"Success: Thumbnail created for video {video_id}"
        finally:
            db.close()
            
    except subprocess.CalledProcessError as e:
        return f"FFmpeg failed: {e.stderr.decode()}"


# ─────────────────────────────────────────────────────────────────────
# Avatar transcode (Phase 2 of Profile_Architecture.md)
# ─────────────────────────────────────────────────────────────────────
# Why a dedicated task instead of reusing process_property_image:
# - The DB write target differs: users.avatar (and the denormalized
#   agents.agent_profile_picture) instead of property_images.thumbnail_url.
# - Avatars live under /uploads/avatars/, not /uploads/images/. Reusing
#   the property task would either misroute the URL or require sentinel
#   IDs threaded through it. A 20-line dedicated task is clearer.
#
# Routed to the existing `media` queue so we don't add a new worker
# topology. Single DB session, single commit — no extra round-trips.
#
# Longest edge of a stored avatar. The largest avatar rendered anywhere in the frontend is 110px
# (audited across every avatar rule in weespas-frontend/src), so 256 covers it at 2x for retina
# with headroom, and no display surface can be upscaling from this.
#
# This bound exists because it was missing: avatars were stored at whatever the camera produced —
# real data showed up to 4160x6240 and a 787 KB mean — then served into a 40px circle on the
# seller console's Viewing Card. Ten live viewers pulled several MB to paint ten thumbnails.
AVATAR_MAX_EDGE = 256


@celery_app.task(name="process_avatar_image", queue="media")
def process_avatar_image(file_path: str, user_id: str):
    """Transcode an uploaded avatar to WebP and update the user row.

    Also syncs the denormalized `agents.agent_profile_picture` column when
    the user is linked to an Agent profile.

    IMPORTANT: we deliberately do NOT delete the original-extension source
    file here. Reason: the upload endpoint returns immediately with the
    original URL (e.g. `<hash>.png`). Any client that has already cached
    that URL — the uploader's own React Query `['auth','me']` snapshot,
    public-agent feeds, and the user's browser image cache keyed by URL —
    will keep requesting `<hash>.png` until it next revalidates. Deleting
    the source here races the client and produces a 404 window where the
    avatar appears blank until the user hard-refreshes.

    Storage cost of keeping the source: bounded to two files per user
    (source + webp) immediately after each upload. The next upload sweeps
    BOTH via the `{user_id}-*` glob in routers/me.py:upload_avatar, so
    long-term steady-state is two files per user-with-an-avatar — a few
    hundred KB each — utterly negligible against the win of never
    serving a broken image.

    The WebP variant is also DOWNSCALED to AVATAR_MAX_EDGE. Doing it here rather than in the
    endpoint keeps POST /me/avatar sub-50ms — this worker already decodes the image to transcode
    it, so bounding the size costs nothing extra. The full-resolution source stays on disk for
    the cache-coherence reason above, and is swept by the next upload's `{user_id}-*` glob.
    """
    output_path = _optimize_one(file_path, max_edge=AVATAR_MAX_EDGE)
    if output_path is None:
        return "missing"

    webp_url = f"/uploads/avatars/{output_path.name}"
    source = Path(file_path)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            # User deleted between upload and transcode — clean up BOTH
            # the orphaned WebP and the source so we don't leak storage.
            output_path.unlink(missing_ok=True)
            if source.exists() and source.resolve() != output_path.resolve():
                source.unlink(missing_ok=True)
            return "no_user"

        user.avatar = webp_url
        if user.agent_id:
            # Bulk UPDATE — avoids loading the Agent row just to mutate
            # one column. PK-indexed, sub-ms.
            db.query(Agent).filter(Agent.id == user.agent_id).update(
                {"agent_profile_picture": webp_url}, synchronize_session=False
            )
        db.commit()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return f"error: {exc}"
    finally:
        db.close()