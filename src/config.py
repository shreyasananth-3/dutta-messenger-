"""Configuration module for DuttaMessenger.

Loads environment variables using Pydantic Settings.
All sensitive values should be set via .env file, never hardcoded.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # FastAPI
    DEBUG: bool = True
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "info"

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://messenger:messenger_pass@localhost:5432/dutta_messenger"
    )
    TEST_DATABASE_URL: str = (
        "postgresql+asyncpg://messenger:messenger_pass@localhost:5432/dutta_messenger_test"
    )
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 0

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_POOL_SIZE: int = 10

    # JWT
    SECRET_KEY: str = "change-me-in-production"  # noqa: S105 - intentional dev default; prod overrides via env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # FCM (Firebase Cloud Messaging)
    FCM_PROJECT_ID: str = ""
    FCM_PRIVATE_KEY_ID: str = ""
    FCM_PRIVATE_KEY: str = ""
    FCM_CLIENT_EMAIL: str = ""

    # S3 / MinIO
    STORAGE_TYPE: str = "minio"  # "minio" or "s3"
    MINIO_URL: str = "http://localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"  # noqa: S105 - MinIO default cred; prod uses AWS IAM
    MINIO_BUCKET: str = "dutta-messenger"
    AWS_S3_BUCKET: str = "dutta-messenger-prod"
    AWS_REGION: str = "us-east-1"

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # File Upload
    MAX_FILE_SIZE: int = 104857600  # 100MB
    ALLOWED_FILE_TYPES: str = "jpg,jpeg,png,gif,pdf,doc,docx,txt,mp3,mp4"

    # API
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    # Pagination
    DEFAULT_PAGE_LIMIT: int = 50
    MAX_PAGE_LIMIT: int = 100

    # Observability
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    OTEL_SERVICE_NAME: str = "dutta-messenger"
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # Security
    SECRETS_BACKEND: str = "env"  # "env" | "aws" | "gcp" | "vault"
    RATE_LIMIT_DEFAULT: str = "300/minute"

    # Feature flags (defaults OFF — each module is turned on explicitly)
    ENABLE_USERS: bool = False
    ENABLE_ACL: bool = False
    ENABLE_GROUPS: bool = False
    ENABLE_CHAT: bool = False
    ENABLE_MEDIA: bool = False
    ENABLE_NOTIFICATIONS: bool = False

    class Config:
        """Pydantic settings configuration."""

        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
