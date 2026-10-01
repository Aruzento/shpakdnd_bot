import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "timers.db"

load_dotenv(ENV_PATH)

TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE_NAME = os.getenv("BOT_TIMEZONE", "Europe/Moscow")

if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN в файле .env")

try:
    TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError as error:
    raise RuntimeError(
        f"Неизвестный часовой пояс: {TIMEZONE_NAME}"
    ) from error


SUPPORTED_DICE = {
    4,
    6,
    8,
    10,
    12,
    20,
    100,
}
