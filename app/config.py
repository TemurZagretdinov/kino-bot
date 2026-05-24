from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")
    database_url: str = Field(
        default="sqlite+aiosqlite:///bot.db",
        alias="DATABASE_URL",
    )
    bot_mode: str = Field(default="polling", alias="BOT_MODE")
    webhook_base_url: str = Field(
        default="https://your-app.onrender.com",
        alias="WEBHOOK_BASE_URL",
    )
    webhook_path: str = Field(default="/webhook", alias="WEBHOOK_PATH")
    webhook_secret: str = Field(default="change_me", alias="WEBHOOK_SECRET")
    port: int = Field(default=8000, alias="PORT")
    archive_channel_ids: str = Field(default="", alias="ARCHIVE_CHANNEL_IDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for raw_id in self.admin_ids_raw.split(","):
            raw_id = raw_id.strip()
            if raw_id.isdigit():
                ids.add(int(raw_id))
        return ids

    @property
    def normalized_bot_mode(self) -> str:
        mode = self.bot_mode.strip().lower()
        if mode not in {"polling", "webhook"}:
            raise RuntimeError("BOT_MODE faqat polling yoki webhook bo'lishi kerak.")
        return mode

    @property
    def normalized_webhook_path(self) -> str:
        path = self.webhook_path.strip() or "/webhook"
        if not path.startswith("/"):
            path = f"/{path}"
        return path

    @property
    def webhook_url(self) -> str:
        base_url = self.webhook_base_url.strip().rstrip("/")
        if not base_url:
            raise RuntimeError("WEBHOOK_BASE_URL bo'sh bo'lmasligi kerak.")
        return f"{base_url}{self.normalized_webhook_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
