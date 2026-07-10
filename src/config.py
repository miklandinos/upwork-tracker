"""Чтение конфигурации из переменных окружения + дефолты Settings.

Секреты берутся ТОЛЬКО из env (локально — из .env через python-dotenv,
в GitHub Actions — из Secrets). Никаких ключей в коде.
"""
import os

try:  # локальная разработка: подхватить .env, если есть
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # в CI dotenv не обязателен
    pass


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value.strip() else default


APIFY_TOKEN = _env("APIFY_TOKEN")
APIFY_ACTOR_ID = _env("APIFY_ACTOR_ID", "flash_mage/upwork")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
AIRTABLE_TOKEN = _env("AIRTABLE_TOKEN")
AIRTABLE_BASE_ID = _env("AIRTABLE_BASE_ID")
AIRTABLE_TABLE = _env("AIRTABLE_TABLE", "Projects")
AIRTABLE_SETTINGS_TABLE = _env("AIRTABLE_SETTINGS_TABLE", "Settings")
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")

# Классификатор (§4.2 ТЗ)
CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
CLASSIFIER_MAX_TOKENS = 400
CLASSIFIER_TEMPERATURE = 0

# Грубый фильтр по ключевым словам (§4.1 ТЗ)
DEFAULT_KEYWORDS = [
    "unreal engine",
    "ue5",
    "ue4",
    "unreal",
    "pixel streaming",
    "archviz",
    "arch viz",
    "architectural visualization",
    "virtual tour",
    "3d tour",
    "real-time 3d",
    "realtime 3d",
    "interactive 3d",
    "twinmotion",
    "metahuman",
]

# Дефолты таблицы Settings (§3.1 ТЗ) — используются, если таблица недоступна
DEFAULT_SETTINGS = {
    "enabled": True,
    "search_queries": ["Unreal Engine"],
    "keywords": list(DEFAULT_KEYWORDS),
    "active_start": "11:00",
    "active_end": "02:00",
    "timezone": "Asia/Bangkok",
    "retention_days": 14,
}

# Сколько вакансий забирать за прогон (минимум актора flash_mage/upwork)
APIFY_ROWS = 5
