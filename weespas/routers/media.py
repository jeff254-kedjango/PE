"""
Media upload endpoints for property images and videos.
Files are stored on the local filesystem under /uploads and served as static files.
Swap the storage path for S3/Cloudinary in production via UPLOAD_DIR env var.
"""

import os
import uuid
import shutil
from pathlib import Path

from PE.weespas.services.image_processing import process_property_images_batch, process_property_video

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Path as PathParam, status
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.services.auth_service import require_agent, verify_property_ownership, get_current_user
from PE.weespas.models.user import User
from PE.weespas.models.property import Property, PropertyImage, PropertyVideo

router = APIRouter()

# ── Storage config ──
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
IMAGE_DIR = UPLOAD_DIR / "images"
VIDEO_DIR = UPLOAD_DIR / "videos"
# Commerce listing media (the §8 seller console) — a SEPARATE subtree from property media so the two
# domains never collide and a future retention/cleanup policy can target one without the other.
TRADE_IMAGE_DIR = UPLOAD_DIR / "trade" / "images"
TRADE_VIDEO_DIR = UPLOAD_DIR / "trade" / "videos"

IMAGE_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
TRADE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
TRADE_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/avif"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100 MB
# A commerce "short video" post is allowed up to 250 MB (product requirement). This is large enough
# that it MUST be streamed to disk (never read fully into RAM) — see upload_trade_media.
MAX_TRADE_VIDEO_SIZE = 250 * 1024 * 1024  # 250 MB
_VIDEO_EXT = {"mp4", "webm", "mov"}
_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "avif"}
_STREAM_CHUNK = 1024 * 1024  # 1 MB — peak RAM per in-flight upload is one chunk, not the whole file


def _get_property_or_404(db: Session, property_id: str) -> Property:
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    return prop


# ===================== IMAGES =====================

@router.post(
    "/properties/{property_id}/images",
    status_code=status.HTTP_201_CREATED,
    summary="Upload images to a property",
    tags=["Media"],
)
def upload_images(
    property_id: str = PathParam(...),
    files: list[UploadFile] = File(..., description="Image files (JPEG, PNG, WebP, AVIF)"),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db),
):
    """
    Upload one or more images to a property. Max 10 MB each.
    The first image uploaded becomes the main image if none exists yet.
    """
    prop = _get_property_or_404(db, property_id)
    verify_property_ownership(current_user, prop)

    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 images per upload")

    has_main = db.query(PropertyImage).filter(
        PropertyImage.property_id == property_id,
        PropertyImage.is_main == True,
    ).first() is not None

    next_order = (
        db.query(PropertyImage)
        .filter(PropertyImage.property_id == property_id)
        .count()
    )

    created = []
    for i, file in enumerate(files):
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"'{file.filename}' has unsupported type '{file.content_type}'. Allowed: JPEG, PNG, WebP, AVIF.",
            )

        # Read into memory and check size
        content = file.file.read()
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"'{file.filename}' exceeds 10 MB limit.",
            )

        ext = (file.filename or "image.jpg").rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp", "avif"):
            ext = "jpg"
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = IMAGE_DIR / filename

        with open(filepath, "wb") as f:
            f.write(content)

        image = PropertyImage(
            property_id=property_id,
            url=f"/uploads/images/{filename}",
            thumbnail_url=f"/uploads/images/{filename}", # This will be updated by the worker later
            original_filename=file.filename,
            file_size=len(content),
            mime_type=file.content_type,
            order=next_order + i,
            is_main=(not has_main and i == 0),
        )
        db.add(image)
        created.append(image)
        if not has_main and i == 0:
            has_main = True

    db.commit()
    batch_payload = []
    for img in created:
        db.refresh(img)
        batch_payload.append({
            "file_path": str(IMAGE_DIR / img.url.split("/")[-1]),
            "image_id": img.id,
        })
    if batch_payload:
        process_property_images_batch.delay(batch_payload)

    return {
        "uploaded": len(created),
        "images": [
            {
                "id": img.id,
                "url": img.url,
                "thumbnail_url": img.thumbnail_url,
                "original_filename": img.original_filename,
                "file_size": img.file_size,
                "mime_type": img.mime_type,
                "order": img.order,
                "is_main": img.is_main,
            }
            for img in created
        ],
    }


@router.delete(
    "/properties/{property_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a property image",
    tags=["Media"],
)
def delete_image(
    property_id: str = PathParam(...),
    image_id: str = PathParam(...),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db),
):
    """Delete a specific image from a property."""
    prop = _get_property_or_404(db, property_id)
    verify_property_ownership(current_user, prop)

    image = db.query(PropertyImage).filter(
        PropertyImage.id == image_id,
        PropertyImage.property_id == property_id,
    ).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Remove file from disk
    if image.url:
        filepath = UPLOAD_DIR.parent / image.url.lstrip("/")
        if filepath.exists():
            filepath.unlink(missing_ok=True)

    db.delete(image)
    db.commit()


# ===================== VIDEOS =====================

@router.post(
    "/properties/{property_id}/videos",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a video to a property",
    tags=["Media"],
)
def upload_video(
    property_id: str = PathParam(...),
    file: UploadFile = File(..., description="Video file (MP4, WebM, MOV)"),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db),
):
    """Upload a single video to a property. Max 100 MB."""
    prop = _get_property_or_404(db, property_id)
    verify_property_ownership(current_user, prop)

    if file.content_type not in ALLOWED_VIDEO_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video type '{file.content_type}'. Allowed: MP4, WebM, MOV.",
        )

    content = file.file.read()
    if len(content) > MAX_VIDEO_SIZE:
        raise HTTPException(status_code=400, detail="Video exceeds 100 MB limit.")

    # Generate unique filename
    ext = (file.filename or "video.mp4").rsplit(".", 1)[-1].lower()
    if ext not in ("mp4", "webm", "mov"):
        ext = "mp4"
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = VIDEO_DIR / filename

    # 1. Save file to disk
    with open(filepath, "wb") as f:
        f.write(content)

    # Calculate order
    next_order = (
        db.query(PropertyVideo)
        .filter(PropertyVideo.property_id == property_id)
        .count()
    )

    # 2. Create the Database Record
    video = PropertyVideo(
        property_id=property_id,
        url=f"/uploads/videos/{filename}",
        thumbnail_url=None,  # Worker will fill this in
        title=file.filename,
        file_size=len(content),
        mime_type=file.content_type,
        order=next_order,
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    # 3. TRIGGER CELERY TASK
    # We pass the ID so the worker can update the DB later
    process_property_video.delay(str(filepath), video.id)

    return {
        "id": video.id,
        "url": video.url,
        "thumbnail_url": video.thumbnail_url,
        "title": video.title,
        "file_size": video.file_size,
        "mime_type": video.mime_type,
        "order": video.order,
    }



@router.delete(
    "/properties/{property_id}/videos/{video_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a property video",
    tags=["Media"],
)
def delete_video(
    property_id: str = PathParam(...),
    video_id: str = PathParam(...),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db),
):
    """Delete a specific video from a property."""
    prop = _get_property_or_404(db, property_id)
    verify_property_ownership(current_user, prop)

    video = db.query(PropertyVideo).filter(
        PropertyVideo.id == video_id,
        PropertyVideo.property_id == property_id,
    ).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.url:
        filepath = UPLOAD_DIR.parent / video.url.lstrip("/")
        if filepath.exists():
            filepath.unlink(missing_ok=True)

    db.delete(video)
    db.commit()


# ===================== TRADE (commerce listing) MEDIA =====================
#
# A GENERIC, any-authenticated-user uploader for commerce listing media (the §8 seller console).
# Distinct from the property uploaders above in three deliberate ways:
#   1. Auth is get_current_user (NOT require_agent): "every house a shop" (§9) — any signed-in
#      weespas user can sell, so any signed-in user can upload listing media. (Cross-service
#      commerce/telemetry tokens are still rejected upstream by get_current_user's scope guard.)
#   2. It persists NO DB row — commerce stores the returned /uploads URL string in listing.media_urls
#      (its own DB). So there is nothing for a Celery task to update; the webp/thumbnail transcode is
#      DEFERRED (v2). A trade image's thumbnail_url == its url for now. (To add later: a
#      process_trade_media task mirroring process_avatar_image, fired here with the saved path.)
#   3. The short video cap is 250 MB and the file is STREAMED to disk with the cap enforced WHILE
#      writing — never file.file.read() (that buffers the whole body in RAM; fine at 10/100 MB,
#      not at 250 MB).
#
# Deploy note: a fronting nginx defaults client_max_body_size to 1 MB — raise it to >=256 MB for
# this route in PE/deploy. Starlette/uvicorn impose no default body cap, so the stream works directly.

def _stream_to_disk(upload: UploadFile, dest: Path, max_bytes: int) -> int:
    """Stream an UploadFile to ``dest`` in fixed chunks, enforcing ``max_bytes`` AS IT WRITES.
    Returns the byte count on success. On overflow (or any mid-stream error) the partial file is
    removed before the exception propagates, so a rejected/failed upload never leaves a stray file.
    Peak memory is one chunk."""
    written = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = upload.file.read(_STREAM_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Video exceeds {max_bytes // (1024 * 1024)} MB limit.",
                    )
                out.write(chunk)
        return written
    except BaseException:
        # HTTPException (overflow), client disconnect, disk-full — clean up the partial file then re-raise.
        dest.unlink(missing_ok=True)
        raise


def _safe_ext(filename: str | None, allowed: set[str], default: str) -> str:
    ext = (filename or f"file.{default}").rsplit(".", 1)[-1].lower()
    return ext if ext in allowed else default


@router.post(
    "/media/trade",
    status_code=status.HTTP_201_CREATED,
    summary="Upload commerce listing media (images + optional short video)",
    tags=["Media"],
)
def upload_trade_media(
    images: list[UploadFile] = File(default=[], description="Listing images (JPEG, PNG, WebP, AVIF; <=10 MB each, max 20)"),
    video: UploadFile | None = File(default=None, description="Optional short video (MP4, WebM, MOV; <=250 MB)"),
    current_user: User = Depends(get_current_user),
):
    """Upload media for a commerce listing and get back /uploads URLs to pass to the commerce
    service's listing-create call. Any authenticated user may call this. Persists no DB row.

    Returns ``{uploaded, images: [...], video: {...}|null}`` — the images array mirrors the
    property image-upload shape so the frontend's resolveMediaUrl works unchanged."""
    if len(images) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 images per upload")
    if not images and video is None:
        raise HTTPException(status_code=400, detail="Provide at least one image or a video.")

    saved_images: list[dict] = []
    written_paths: list[Path] = []
    try:
        for file in images:
            if file.content_type not in ALLOWED_IMAGE_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{file.filename}' has unsupported type '{file.content_type}'. Allowed: JPEG, PNG, WebP, AVIF.",
                )
            ext = _safe_ext(file.filename, _IMAGE_EXT, "jpg")
            filename = f"{uuid.uuid4().hex}.{ext}"
            dest = TRADE_IMAGE_DIR / filename
            size = _stream_to_disk(file, dest, MAX_IMAGE_SIZE)
            written_paths.append(dest)
            url = f"/uploads/trade/images/{filename}"
            saved_images.append({
                "url": url,
                "thumbnail_url": url,  # transcode deferred (v2) — no DB row to update
                "original_filename": file.filename,
                "file_size": size,
                "mime_type": file.content_type,
            })

        saved_video: dict | None = None
        if video is not None:
            if video.content_type not in ALLOWED_VIDEO_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported video type '{video.content_type}'. Allowed: MP4, WebM, MOV.",
                )
            ext = _safe_ext(video.filename, _VIDEO_EXT, "mp4")
            filename = f"{uuid.uuid4().hex}.{ext}"
            dest = TRADE_VIDEO_DIR / filename
            size = _stream_to_disk(video, dest, MAX_TRADE_VIDEO_SIZE)
            written_paths.append(dest)
            url = f"/uploads/trade/videos/{filename}"
            saved_video = {
                "url": url,
                "thumbnail_url": url,
                "original_filename": video.filename,
                "file_size": size,
                "mime_type": video.content_type,
            }
    except BaseException:
        # A later file failing must not leave earlier files orphaned — roll back the whole upload.
        for p in written_paths:
            p.unlink(missing_ok=True)
        raise

    return {
        "uploaded": len(saved_images) + (1 if saved_video else 0),
        "images": saved_images,
        "video": saved_video,
    }
