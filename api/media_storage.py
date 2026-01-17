import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MediaLocation:
    """
    Represents where a media object lives.
    - object_key: identifier used by the storage backend (local relative path or S3 key).
    - public_url: absolute URL clients can fetch (best-effort).
    """
    object_key: str
    public_url: Optional[str]


class MediaStorage:
    def put_bytes(self, object_key: str, data: bytes, content_type: str) -> MediaLocation:
        raise NotImplementedError

    def delete(self, object_key: str) -> None:
        raise NotImplementedError

    def public_url_for(self, object_key: str) -> Optional[str]:
        raise NotImplementedError

    def exists(self, object_key: str) -> bool:
        """Best-effort existence check (used for avatar lookup)."""
        return False


class LocalMediaStorage(MediaStorage):
    def __init__(self, media_root: str, public_prefix: str = "/media-files"):
        self.media_root = media_root
        self.public_prefix = public_prefix.rstrip("/")

    def _abs_path(self, object_key: str) -> str:
        object_key = object_key.lstrip("/")
        return os.path.join(self.media_root, object_key)

    def put_bytes(self, object_key: str, data: bytes, content_type: str) -> MediaLocation:
        path = self._abs_path(object_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return MediaLocation(object_key=object_key, public_url=self.public_url_for(object_key))

    def delete(self, object_key: str) -> None:
        path = self._abs_path(object_key)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def public_url_for(self, object_key: str) -> Optional[str]:
        object_key = object_key.lstrip("/")
        return f"{self.public_prefix}/{object_key}"

    def exists(self, object_key: str) -> bool:
        path = self._abs_path(object_key)
        return os.path.exists(path)


class S3MediaStorage(MediaStorage):
    """
    S3-compatible storage (AWS S3, Cloudflare R2, Backblaze B2 S3 API, etc.).
    Requires env vars:
      - BCR_S3_BUCKET
      - BCR_S3_ACCESS_KEY_ID
      - BCR_S3_SECRET_ACCESS_KEY
    Optional:
      - BCR_S3_REGION (default: auto)
      - BCR_S3_ENDPOINT_URL (for R2, etc.)
      - BCR_S3_PUBLIC_BASE_URL (recommended): absolute base URL to serve objects
        Example: https://<public-domain>  (then objects are at {base}/{key})
    """

    def __init__(
        self,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "auto",
        endpoint_url: Optional[str] = None,
        public_base_url: Optional[str] = None,
    ):
        import boto3

        self.bucket = bucket
        self.public_base_url = (public_base_url or "").rstrip("/") or None
        self.use_acl_public_read = (os.getenv("BCR_S3_USE_ACL_PUBLIC_READ") or "false").strip().lower() == "true"
        self.s3 = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def put_bytes(self, object_key: str, data: bytes, content_type: str) -> MediaLocation:
        key = object_key.lstrip("/")
        kwargs = dict(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        # Some S3-compatible providers (ex: Cloudflare R2) don't support ACLs.
        if self.use_acl_public_read:
            kwargs["ACL"] = "public-read"
        self.s3.put_object(**kwargs)
        return MediaLocation(object_key=key, public_url=self.public_url_for(key))

    def delete(self, object_key: str) -> None:
        key = object_key.lstrip("/")
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            pass

    def public_url_for(self, object_key: str) -> Optional[str]:
        key = object_key.lstrip("/")
        if self.public_base_url:
            return f"{self.public_base_url}/{key}"
        return None

    def exists(self, object_key: str) -> bool:
        key = object_key.lstrip("/")
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


def build_media_storage(media_root: str) -> MediaStorage:
    backend = (os.getenv("BCR_MEDIA_BACKEND") or "local").strip().lower()

    if backend == "s3":
        bucket = (os.getenv("BCR_S3_BUCKET") or "").strip()
        access = (os.getenv("BCR_S3_ACCESS_KEY_ID") or "").strip()
        secret = (os.getenv("BCR_S3_SECRET_ACCESS_KEY") or "").strip()
        region = (os.getenv("BCR_S3_REGION") or "auto").strip()
        endpoint_url = (os.getenv("BCR_S3_ENDPOINT_URL") or "").strip() or None
        public_base_url = (os.getenv("BCR_S3_PUBLIC_BASE_URL") or "").strip() or None

        if not bucket or not access or not secret:
            raise RuntimeError("S3 backend requires BCR_S3_BUCKET, BCR_S3_ACCESS_KEY_ID, BCR_S3_SECRET_ACCESS_KEY")

        return S3MediaStorage(
            bucket=bucket,
            access_key_id=access,
            secret_access_key=secret,
            region=region,
            endpoint_url=endpoint_url,
            public_base_url=public_base_url,
        )

    # default: local
    return LocalMediaStorage(media_root=media_root, public_prefix="/media-files")

