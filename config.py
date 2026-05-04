# config.py
# Configuración centralizada — carga variables de entorno

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str
    META_VERIFY_TOKEN: str = "nodia_verify_token_2024"
    META_APP_SECRET: str = ""
    META_APP_ID: str = ""
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o-mini"
    PORT: int = 8000
    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
