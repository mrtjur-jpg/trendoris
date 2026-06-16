from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Shopify
    shopify_store: str = "trendorius.myshopify.com"
    shopify_access_token: str = ""
    # Webhook signing secret — Shopify Admin -> Settings -> Notifications -> Webhooks
    shopify_webhook_secret: str = ""

    # Ochrana /admin/* endpointov — ak je nastavený, vyžaduje sa
    # hlavička "Authorization: Bearer <token>". Na produkcii POVINNÉ.
    admin_token: str = ""

    # CJ Dropshipping
    cj_api_key: str = ""
    cj_email: str = ""

    # Google Gemini
    gemini_api_key: str = ""

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "trendoriuso-trend-agent/1.0"

    # Database — default SQLite pre lokálny vývoj, na produkcii nastav PostgreSQL v .env
    database_url: str = "sqlite+aiosqlite:///trendoris.db"

    # Catalog rules
    catalog_size: int = 50
    daily_refresh_count: int = 5
    cron_hour: int = 6
    cron_minute: int = 0

    # Mock režim — celý pipeline beží s falošnými dátami, bez API kľúčov.
    mock_mode: bool = False


settings = Settings()
