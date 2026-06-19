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

    # SMTP Configurations for Email Notifications
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_SENDER: str = ""  # e.g., "BeautySync Pro Alertas <alertas@beautysyncpro.app>"
    NOTIFY_EMAIL: str = ""  # recipient for qualified leads
    SALES_TENANT_ID: str = "3273dbab-9d62-4d3e-84ef-2d462b1ede0a"  # Exclusive tenant

    class Config:
        env_file = ".env"


settings = Settings()

