import io
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

from .client import PolarstepsAPIClient
from .exif_constants import (
    EXIF_IFD_EXIF,
    EXIF_TAG_DATETIME,
    EXIF_TAG_DATETIME_ORIGINAL,
)

# Thumbnail dimensions and JPEG quality levels
_LARGE_THUMB_SIZE    = (1280, 1280)
_LARGE_THUMB_QUALITY = 85
_SMALL_THUMB_SIZE    = (300, 300)
_SMALL_THUMB_QUALITY = 80
_FULL_RES_QUALITY    = 95


@dataclass
class ImageData:
    """Holds all derived data extracted from a local image file."""
    full_bytes:  bytes
    large_bytes: bytes
    small_bytes: bytes
    width:       int
    height:      int
    taken_time:  str


def extract_image_data(file_path: str) -> ImageData:
    """
    Loads a local image file, auto-transposes orientation from EXIF,
    extracts dimensions and taken_time, and generates:
      - full_res bytes
      - large_thumb bytes (max 1280×1280)
      - small_thumb bytes (max 300×300)
    Returns an ImageData dataclass instance.
    """
    if Image is None:
        raise ImportError(
            "The 'Pillow' library is required to process and upload media.\n"
            "Please install it using: pip install Pillow"
        )

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Image file not found: {file_path}")

    with Image.open(file_path) as raw_img:
        img = ImageOps.exif_transpose(raw_img)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        width, height = img.size

        # Extract EXIF taken_time — try SubIFD first, then root IFD
        taken_time = None
        try:
            exif = img.getexif()
            if exif:
                sub_ifd = exif.get_ifd(EXIF_IFD_EXIF) or {}
                date_str = (
                    sub_ifd.get(EXIF_TAG_DATETIME_ORIGINAL)
                    or exif.get(EXIF_TAG_DATETIME_ORIGINAL)
                    or exif.get(EXIF_TAG_DATETIME)
                )
                if date_str:
                    dt = datetime.strptime(str(date_str)[:19], "%Y:%m:%d %H:%M:%S")
                    taken_time = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except Exception:
            pass

        if not taken_time:
            mtime = os.path.getmtime(file_path)
            taken_time = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            print(f"⚠️ [Media] No EXIF date found in '{os.path.basename(file_path)}'. Using file modification time: {taken_time}")

        # Prepare full_res bytes (keep original bytes if already JPEG, else encode)
        _, ext = os.path.splitext(file_path.lower())
        if ext in (".jpg", ".jpeg"):
            with open(file_path, "rb") as f:
                full_bytes = f.read()
        else:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_FULL_RES_QUALITY)
            full_bytes = buf.getvalue()

        # Generate large thumbnail
        large_img = img.copy()
        large_img.thumbnail(_LARGE_THUMB_SIZE, Image.Resampling.LANCZOS)
        buf_large = io.BytesIO()
        large_img.save(buf_large, format="JPEG", quality=_LARGE_THUMB_QUALITY)
        large_bytes = buf_large.getvalue()

        # Generate small thumbnail
        small_img = img.copy()
        small_img.thumbnail(_SMALL_THUMB_SIZE, Image.Resampling.LANCZOS)
        buf_small = io.BytesIO()
        small_img.save(buf_small, format="JPEG", quality=_SMALL_THUMB_QUALITY)
        small_bytes = buf_small.getvalue()

    return ImageData(
        full_bytes=full_bytes,
        large_bytes=large_bytes,
        small_bytes=small_bytes,
        width=width,
        height=height,
        taken_time=taken_time,
    )


def process_and_upload_image(
    file_path: str,
    order: int,
    polarsteps_client: PolarstepsAPIClient,
    trip_id: int
) -> Dict[str, Any]:
    """
    Processes an image, signs S3 upload URLs for its 3 variants,
    uploads all variants to S3, and returns the media_info dictionary.
    """
    filename = os.path.basename(file_path)
    print(f"📷 Processing '{filename}' (order: {order})...")

    image_data = extract_image_data(file_path)
    print(f"   Dimensions: {image_data.width}x{image_data.height}, Taken: {image_data.taken_time}")

    media_uuid = str(uuid.uuid4())
    s3_obj_full  = f"{media_uuid}_{uuid.uuid4()}"
    s3_obj_large = f"{media_uuid}_{uuid.uuid4()}"
    s3_obj_small = f"{media_uuid}_{uuid.uuid4()}"

    # 1. Sign & upload full resolution image
    sign_full = polarsteps_client.sign_s3(s3_obj_full, "image/jpeg", "full_res_step_image", trip_id=trip_id)
    polarsteps_client.upload_to_s3(sign_full["signed_request"], image_data.full_bytes, "image/jpeg")

    # 2. Sign & upload large thumbnail
    sign_large = polarsteps_client.sign_s3(s3_obj_large, "image/jpeg", "large_thumb_step_image", trip_id=trip_id)
    polarsteps_client.upload_to_s3(sign_large["signed_request"], image_data.large_bytes, "image/jpeg")

    # 3. Sign & upload small thumbnail
    sign_small = polarsteps_client.sign_s3(s3_obj_small, "image/jpeg", "small_thumb_step_image", trip_id=trip_id)
    polarsteps_client.upload_to_s3(sign_small["signed_request"], image_data.small_bytes, "image/jpeg")

    print(f"   ✅ Successfully uploaded 3 variants to S3 for '{filename}'")

    return {
        "image_url":          sign_full["url"],
        "big_thumbnail_url":  sign_large["url"],
        "small_thumbnail_url": sign_small["url"],
        "full_res_width":     image_data.width,
        "full_res_height":    image_data.height,
        "uuid":               media_uuid,
        "order":              order,
        "taken_time":         image_data.taken_time,
    }
