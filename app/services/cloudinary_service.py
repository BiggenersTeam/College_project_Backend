"""
FaceTracer Cloudinary Service
==============================
Handles all image uploads to Cloudinary for persistent storage.

Why Cloudinary:
  Render's filesystem is ephemeral — every deploy/restart wipes local files.
  Cloudinary stores images permanently and returns a stable HTTPS URL that
  works across all deploys, restarts, and browsers.

Strategy:
  1. Save file to local disk temporarily (required for OpenCV face encoding)
  2. Upload the local file to Cloudinary
  3. Store the returned HTTPS URL in MongoDB (replaces the relative path)
  4. Delete the local temp file
  5. On Cloudinary failure → keep the local file and store relative path
     (graceful degradation — works in dev / local environments)

Configuration (environment variables):
  CLOUDINARY_CLOUD_NAME  — your Cloudinary cloud name
  CLOUDINARY_API_KEY     — your Cloudinary API key
  CLOUDINARY_API_SECRET  — your Cloudinary API secret

All three must be set for uploads to succeed. If any is missing, the service
returns None and the caller falls back to local path storage.
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _is_configured() -> bool:
    """Return True if all three Cloudinary credentials are present."""
    return all([
        os.getenv("CLOUDINARY_CLOUD_NAME"),
        os.getenv("CLOUDINARY_API_KEY"),
        os.getenv("CLOUDINARY_API_SECRET"),
    ])


def _get_cloudinary():
    """
    Lazily import and configure cloudinary.
    Returns the cloudinary.uploader module or None if not available.
    """
    if not _is_configured():
        return None
    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(
            cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
            api_key=os.getenv("CLOUDINARY_API_KEY"),
            api_secret=os.getenv("CLOUDINARY_API_SECRET"),
            secure=True,          # always use HTTPS URLs
        )
        return cloudinary.uploader
    except ImportError:
        logger.warning(
            "[Cloudinary] cloudinary package not installed. "
            "Run: pip install cloudinary>=1.36.0"
        )
        return None
    except Exception as exc:
        logger.error(f"[Cloudinary] Configuration failed: {exc}")
        return None


def upload_image(
    local_path: str,
    folder: str = "facetracer",
    public_id: Optional[str] = None,
    delete_local: bool = True,
) -> Optional[str]:
    """
    Upload a local image file to Cloudinary.

    Args:
        local_path:    Absolute path to the local image file.
        folder:        Cloudinary folder to organise uploads.
                       e.g. "facetracer/profiles", "facetracer/cases"
        public_id:     Optional stable Cloudinary public ID (used for
                       profile photos so repeated uploads replace the same asset).
        delete_local:  If True, delete the local file after a successful upload.
                       Set False when the file is still needed (e.g. face encoding
                       extraction happens after this call).

    Returns:
        str  — permanent Cloudinary HTTPS URL  (e.g. https://res.cloudinary.com/...)
        None — if Cloudinary is not configured or upload fails
               (caller should fall back to local relative path)
    """
    uploader = _get_cloudinary()
    if uploader is None:
        logger.debug("[Cloudinary] Not configured — skipping cloud upload.")
        return None

    if not os.path.exists(local_path):
        logger.error(f"[Cloudinary] Local file not found: {local_path}")
        return None

    try:
        upload_kwargs: dict = {
            "folder": folder,
            "overwrite": True,
            "resource_type": "image",
        }
        if public_id:
            upload_kwargs["public_id"] = public_id

        result = uploader.upload(local_path, **upload_kwargs)
        url: str = result.get("secure_url", "")

        if not url:
            logger.error(f"[Cloudinary] Upload succeeded but no URL in response: {result}")
            return None

        logger.info(f"[Cloudinary] Uploaded: {os.path.basename(local_path)} → {url}")

        # Clean up local temp file after successful cloud upload
        if delete_local:
            try:
                os.remove(local_path)
                logger.debug(f"[Cloudinary] Local temp file deleted: {local_path}")
            except Exception as del_err:
                logger.warning(f"[Cloudinary] Could not delete local file {local_path}: {del_err}")

        return url

    except Exception as exc:
        logger.error(f"[Cloudinary] Upload failed for {local_path}: {exc}", exc_info=True)
        return None


def upload_image_bytes(
    image_bytes: bytes,
    filename: str,
    folder: str = "facetracer",
    public_id: Optional[str] = None,
) -> Optional[str]:
    """
    Upload image bytes directly to Cloudinary without writing to disk.
    Used for profile photos and other cases where no local file is needed.

    Args:
        image_bytes: Raw image bytes.
        filename:    Original filename (used to infer format).
        folder:      Cloudinary folder.
        public_id:   Optional stable Cloudinary public ID.

    Returns:
        Permanent Cloudinary HTTPS URL or None on failure.
    """
    uploader = _get_cloudinary()
    if uploader is None:
        return None

    try:
        import io
        upload_kwargs: dict = {
            "folder": folder,
            "overwrite": True,
            "resource_type": "image",
        }
        if public_id:
            upload_kwargs["public_id"] = public_id

        result = uploader.upload(io.BytesIO(image_bytes), **upload_kwargs)
        url: str = result.get("secure_url", "")

        if not url:
            logger.error(f"[Cloudinary] Bytes upload returned no URL: {result}")
            return None

        logger.info(f"[Cloudinary] Bytes uploaded: {filename} → {url}")
        return url

    except Exception as exc:
        logger.error(f"[Cloudinary] Bytes upload failed for {filename}: {exc}", exc_info=True)
        return None


def is_cloudinary_url(path: str) -> bool:
    """Return True if a stored path is already a Cloudinary HTTPS URL."""
    return isinstance(path, str) and "res.cloudinary.com" in path
