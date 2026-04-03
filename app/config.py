import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://postgres:postgres@localhost:5434/dotmac_crm",
        )
    )
    db_pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "15")))
    db_max_overflow: int = field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "20")))
    db_pool_timeout: int = field(default_factory=lambda: int(os.getenv("DB_POOL_TIMEOUT", "30")))
    db_pool_recycle: int = field(default_factory=lambda: int(os.getenv("DB_POOL_RECYCLE", "1800")))

    # Avatar settings
    avatar_upload_dir: str = field(default_factory=lambda: os.getenv("AVATAR_UPLOAD_DIR", "static/avatars"))
    avatar_max_size_bytes: int = field(
        default_factory=lambda: int(os.getenv("AVATAR_MAX_SIZE_BYTES", str(2 * 1024 * 1024)))
    )  # 2MB
    avatar_allowed_types: str = field(
        default_factory=lambda: os.getenv("AVATAR_ALLOWED_TYPES", "image/jpeg,image/png,image/gif,image/webp")
    )
    avatar_url_prefix: str = field(default_factory=lambda: os.getenv("AVATAR_URL_PREFIX", "/static/avatars"))

    # DEM settings
    dem_data_dir: str = field(default_factory=lambda: os.getenv("DEM_DATA_DIR", "data/dem/srtm"))

    # Ticket attachment settings
    ticket_attachment_upload_dir: str = field(
        default_factory=lambda: os.getenv("TICKET_ATTACHMENT_UPLOAD_DIR", "static/uploads/tickets")
    )
    ticket_attachment_url_prefix: str = field(
        default_factory=lambda: os.getenv("TICKET_ATTACHMENT_URL_PREFIX", "/static/uploads/tickets")
    )
    ticket_attachment_max_size_bytes: int = field(
        default_factory=lambda: int(os.getenv("TICKET_ATTACHMENT_MAX_SIZE_BYTES", str(5 * 1024 * 1024)))
    )
    ticket_attachment_allowed_types: str = field(
        default_factory=lambda: os.getenv(
            "TICKET_ATTACHMENT_ALLOWED_TYPES",
            "image/jpeg,image/png,image/gif,image/webp,application/pdf",
        )
    )

    # CRM message attachment settings
    message_attachment_upload_dir: str = field(
        default_factory=lambda: os.getenv("MESSAGE_ATTACHMENT_UPLOAD_DIR", "static/uploads/messages")
    )
    message_attachment_url_prefix: str = field(
        default_factory=lambda: os.getenv("MESSAGE_ATTACHMENT_URL_PREFIX", "/static/uploads/messages")
    )
    message_attachment_max_size_bytes: int = field(
        default_factory=lambda: int(os.getenv("MESSAGE_ATTACHMENT_MAX_SIZE_BYTES", str(5 * 1024 * 1024)))
    )
    message_attachment_allowed_types: str = field(
        default_factory=lambda: os.getenv(
            "MESSAGE_ATTACHMENT_ALLOWED_TYPES",
            "image/jpeg,image/png,image/gif,image/webp,image/heic,image/heif,image/heic-sequence,image/heif-sequence,application/pdf",
        )
    )

    # Branding assets (logo & favicon)
    branding_upload_dir: str = field(
        default_factory=lambda: os.getenv("BRANDING_UPLOAD_DIR", "static/uploads/branding")
    )
    branding_url_prefix: str = field(
        default_factory=lambda: os.getenv("BRANDING_URL_PREFIX", "/static/uploads/branding")
    )
    branding_logo_max_size_bytes: int = field(
        default_factory=lambda: int(os.getenv("BRANDING_LOGO_MAX_SIZE_BYTES", str(2 * 1024 * 1024)))
    )
    branding_favicon_max_size_bytes: int = field(
        default_factory=lambda: int(os.getenv("BRANDING_FAVICON_MAX_SIZE_BYTES", str(512 * 1024)))
    )

    # Meta Graph API settings
    meta_graph_api_version: str = field(default_factory=lambda: os.getenv("META_GRAPH_API_VERSION", "v19.0"))
    meta_graph_base_url: str = field(
        default_factory=lambda: os.getenv(
            "META_GRAPH_BASE_URL",
            f"https://graph.facebook.com/{os.getenv('META_GRAPH_API_VERSION', 'v19.0')}",
        )
    )

    # Storage backend
    storage_backend: str = field(default_factory=lambda: os.getenv("STORAGE_BACKEND", "local"))  # "local" or "s3"
    storage_local_root: str = field(default_factory=lambda: os.getenv("STORAGE_LOCAL_ROOT", "static"))
    storage_local_url_prefix: str = field(default_factory=lambda: os.getenv("STORAGE_LOCAL_URL_PREFIX", "/static"))

    # S3 / MinIO settings (only used when storage_backend = "s3")
    s3_endpoint_url: str = field(default_factory=lambda: os.getenv("S3_ENDPOINT_URL", "http://minio:9000"))
    s3_access_key: str = field(default_factory=lambda: os.getenv("S3_ACCESS_KEY", ""))
    s3_secret_key: str = field(default_factory=lambda: os.getenv("S3_SECRET_KEY", ""))
    s3_bucket: str = field(default_factory=lambda: os.getenv("S3_BUCKET", "dotmac-uploads"))
    s3_region: str = field(default_factory=lambda: os.getenv("S3_REGION", "us-east-1"))
    s3_public_url: str = field(default_factory=lambda: os.getenv("S3_PUBLIC_URL", "http://localhost:9000"))

    # ERPNext integration settings
    erpnext_url: str | None = field(default_factory=lambda: os.getenv("ERPNEXT_URL"))
    erpnext_api_key: str | None = field(default_factory=lambda: os.getenv("ERPNEXT_API_KEY"))
    erpnext_api_secret: str | None = field(default_factory=lambda: os.getenv("ERPNEXT_API_SECRET"))

    # Cookie security settings
    cookie_secure: bool = field(default_factory=lambda: _env_bool("COOKIE_SECURE", default=False))


settings = Settings()
