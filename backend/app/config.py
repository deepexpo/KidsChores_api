"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    database_url: str

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 60
    jwt_refresh_expire_days: int = 30

    # Apple Sign In
    apple_bundle_id: str = "com.kidschores.app"
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key_path: str = ""

    # APNs
    apns_bundle_id: str = "com.kidschores.app"
    apns_env: str = "sandbox"  # "sandbox" | "production"

    # App
    app_env: str = "development"
    app_debug: bool = True
    allowed_origins: list[str] = ["http://localhost:3000"]


settings = Settings()  # type: ignore[call-arg]
