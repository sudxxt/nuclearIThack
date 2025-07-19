from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Filled automatically by assistant ---
    BOT_TOKEN: str = "8125435185:AAF01kQu_yz8k5oC3eLECo6EVt3BoNfrCy0"
    ADMIN_IDS: set[int] = {6433063981}
    ADMIN_CHAT_ID: int = -1001234567890
    DB_PATH: Path = Path.home() / "donor.db"
    WHISPER_MODEL: str = "small"
    OPENROUTER_API_KEY: str | None = "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"
    OPENROUTER_MODEL: str = "mistralai/mistral-small-3.2-24b-instruct:free"
    # Port on which the built-in static server for mini-apps will run
    LOCAL_WEBAPP_PORT: int = 8080

    # Base URL to the Web App that shows the user‐agreement (mini-app).
    # It defaults to the local server so the feature works without a public domain.
    AGREEMENT_WEBAPP_URL: str = "http://localhost:8080/user_agreement.html"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
