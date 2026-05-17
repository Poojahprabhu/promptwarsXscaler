"""Helpers for uploading extracted assets to Google Cloud Storage."""
import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from google.cloud import storage

logger = logging.getLogger(__name__)

_client: storage.Client | None = None


def _get_client() -> storage.Client:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = storage.Client()
    return _client


def upload_bytes_to_gcs(data: bytes, gcs_object_name: str, content_type: str | None = None) -> str:
    """Upload raw bytes to GCS and return the gs:// URI."""
    bucket_name = settings.GCP_STORAGE_BUCKET_NAME
    if not bucket_name:
        raise ValueError("GCP_STORAGE_BUCKET_NAME is not configured")

    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_object_name)

    if content_type is None:
        content_type, _ = mimetypes.guess_type(gcs_object_name)

    blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
    gcs_uri = f"gs://{bucket_name}/{gcs_object_name}"
    logger.info("Uploaded %d bytes to %s", len(data), gcs_uri)
    return gcs_uri


def upload_file_to_gcs(local_path: Path, gcs_object_name: str) -> str:
    """Upload a local file to GCS and return the gs:// URI."""
    data = local_path.read_bytes()
    content_type, _ = mimetypes.guess_type(str(local_path))
    return upload_bytes_to_gcs(data, gcs_object_name, content_type)


def build_image_gcs_path(document_id: str, page: int, index: int, extension: str = "png") -> str:
    """Return a deterministic GCS object path for an extracted page image."""
    return f"documents/{document_id}/images/page{page:04d}_img{index:02d}.{extension}"
