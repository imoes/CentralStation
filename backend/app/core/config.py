from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Nur diese 4 Werte kommen aus ENV/.env
    # Alles andere (Connector-URLs, LLM-Endpoints, etc.) kommt aus der DB
    database_url: str
    redis_url: str
    secret_key: str
    encryption_key: str

    # JWT
    access_token_expire_minutes: int = 480  # 8 hours
    refresh_token_expire_days: int = 7
    algorithm: str = "HS256"

    # App
    app_name: str = "CentralStation"
    debug: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
