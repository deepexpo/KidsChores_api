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
    apple_bundle_id: str = "com.klsingh.KidsChores"
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key_path: str = ""

    # APNs — token-based auth (Auth Key, not a certificate). Unset in any of
    # these three means "not configured": push.py falls back to logging
    # instead of sending, so local dev / a not-yet-enrolled Apple Developer
    # account never blocks anything.
    apns_bundle_id: str = "com.klsingh.KidsChores"
    apns_env: str = "sandbox"  # "sandbox" | "production"
    apns_team_id: str = ""
    apns_key_id: str = ""
    apns_private_key: str = ""  # PEM content of the .p8 Auth Key, not a file path

    # App
    app_env: str = "development"
    app_debug: bool = True
    allowed_origins: list[str] = ["http://localhost:3000"]


settings = Settings()  # type: ignore[call-arg]
