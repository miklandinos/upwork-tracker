"""Разовое авто-создание таблиц Projects и Settings по схеме §3/§3.1 ТЗ.

Запуск:  python bootstrap_airtable.py
Требуется AIRTABLE_TOKEN со scope schema.bases:write и AIRTABLE_BASE_ID (пустая база).

Идемпотентно: существующие таблицы/поля пропускаются, повторный запуск безопасен.
Представления (views) Web API создавать не умеет — см. README, раздел «Views».
"""
from __future__ import annotations

import sys

import requests

from src import config

META_TABLES_URL = "https://api.airtable.com/v0/meta/bases/{base}/tables"
META_FIELDS_URL = "https://api.airtable.com/v0/meta/bases/{base}/tables/{table}/fields"
DATA_URL = "https://api.airtable.com/v0/{base}/{table}"

DATETIME_OPTIONS = {
    "timeZone": "client",
    "dateFormat": {"name": "iso"},
    "timeFormat": {"name": "24hour"},
}

PROJECTS_FIELDS = [
    {"name": "Upwork ID", "type": "singleLineText"},  # primary, ключ дедупликации
    {"name": "Title", "type": "singleLineText"},
    {"name": "URL", "type": "url"},
    {
        "name": "Category",
        "type": "singleSelect",
        "options": {"choices": [
            {"name": "Недвижимость"},
            {"name": "Геймдев"},
            {"name": "Другие сферы"},
            {"name": "Веб-интерактив"},
        ]},
    },
    {"name": "Summary RU", "type": "multilineText"},
    {"name": "Budget", "type": "singleLineText"},
    {
        "name": "Job Type",
        "type": "singleSelect",
        "options": {"choices": [{"name": "Fixed"}, {"name": "Hourly"}]},
    },
    {"name": "Country", "type": "singleLineText"},
    {"name": "Client Spent", "type": "singleLineText"},
    {"name": "Posted At", "type": "dateTime", "options": DATETIME_OPTIONS},
    {
        "name": "Status",
        "type": "singleSelect",
        "options": {"choices": [
            {"name": "Новый"},
            {"name": "Интересует"},
            {"name": "Удалён"},
        ]},
    },
    {"name": "Bids", "type": "number", "options": {"precision": 0}},  # откликов на момент добавления
    {"name": "AI Confidence", "type": "number", "options": {"precision": 2}},
    {"name": "AI Reason", "type": "multilineText"},
    {"name": "Notified", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}},
    # Meta API не умеет создавать поля типа createdTime — используем эквивалентную
    # формулу CREATED_TIME(): для фильтров views и retention работает так же
    {
        "name": "Created At",
        "type": "formula",
        "options": {"formula": "CREATED_TIME()"},
    },
]

SETTINGS_FIELDS = [
    {"name": "Name", "type": "singleLineText"},  # primary (checkbox не может быть primary)
    {"name": "Enabled", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}},
    {"name": "Search Queries", "type": "multilineText"},
    {"name": "Keyword Filter", "type": "multilineText"},
    {"name": "Active Start", "type": "singleLineText"},
    {"name": "Active End", "type": "singleLineText"},
    {"name": "Timezone", "type": "singleLineText"},
    {"name": "Retention Days", "type": "number", "options": {"precision": 0}},
    # Heartbeat: скрипт отмечает здесь время и итог последнего скана
    {"name": "Last Scan At", "type": "dateTime", "options": DATETIME_OPTIONS},
    {"name": "Last Scan Result", "type": "singleLineText"},
]

DEFAULT_SETTINGS_RECORD = {
    "Name": "Default",
    "Enabled": True,
    "Search Queries": "\n".join(config.DEFAULT_SETTINGS["search_queries"]),
    "Keyword Filter": ", ".join(config.DEFAULT_KEYWORDS),
    "Active Start": config.DEFAULT_SETTINGS["active_start"],
    "Active End": config.DEFAULT_SETTINGS["active_end"],
    "Timezone": config.DEFAULT_SETTINGS["timezone"],
    "Retention Days": config.DEFAULT_SETTINGS["retention_days"],
}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.AIRTABLE_TOKEN}",
        "Content-Type": "application/json",
    }


def _get_existing_tables(base_id: str) -> dict[str, dict]:
    resp = requests.get(META_TABLES_URL.format(base=base_id), headers=_headers(), timeout=60)
    resp.raise_for_status()
    return {table["name"]: table for table in resp.json()["tables"]}


def _create_table(base_id: str, name: str, fields: list[dict]) -> dict:
    resp = requests.post(
        META_TABLES_URL.format(base=base_id),
        headers=_headers(),
        json={"name": name, "fields": fields},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _add_missing_fields(base_id: str, table: dict, fields: list[dict]) -> None:
    existing_names = {field["name"] for field in table["fields"]}
    for field in fields:
        if field["name"] in existing_names:
            continue
        resp = requests.post(
            META_FIELDS_URL.format(base=base_id, table=table["id"]),
            headers=_headers(),
            json=field,
            timeout=60,
        )
        if resp.ok:
            print(f"  + поле {field['name']!r}")
        else:
            print(f"  ! не удалось создать поле {field['name']!r}: {resp.status_code} {resp.text}")
            print(f"    Добавьте его вручную (тип: {field['type']})")


def _seed_settings_record(base_id: str, table_name: str) -> None:
    url = DATA_URL.format(base=base_id, table=table_name)
    resp = requests.get(url, headers=_headers(), params={"maxRecords": 1}, timeout=60)
    resp.raise_for_status()
    if resp.json().get("records"):
        print("  Settings уже содержит запись — пропускаю")
        return
    resp = requests.post(
        url,
        headers=_headers(),
        json={"records": [{"fields": DEFAULT_SETTINGS_RECORD}], "typecast": True},
        timeout=60,
    )
    resp.raise_for_status()
    print("  + запись Settings с дефолтами (§3.1)")


def main() -> int:
    if not config.AIRTABLE_TOKEN or not config.AIRTABLE_BASE_ID:
        print("Задайте AIRTABLE_TOKEN и AIRTABLE_BASE_ID (env или .env)")
        return 1

    base_id = config.AIRTABLE_BASE_ID
    tables = _get_existing_tables(base_id)

    for name, fields in (
        (config.AIRTABLE_TABLE, PROJECTS_FIELDS),
        (config.AIRTABLE_SETTINGS_TABLE, SETTINGS_FIELDS),
    ):
        if name in tables:
            print(f"Таблица {name!r} уже существует — проверяю поля")
            _add_missing_fields(base_id, tables[name], fields)
        else:
            print(f"Создаю таблицу {name!r}")
            _create_table(base_id, name, fields)

    print("Заполняю Settings дефолтами")
    _seed_settings_record(base_id, config.AIRTABLE_SETTINGS_TABLE)

    print("\nГотово. Не забудьте создать представления (views) вручную — см. README.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
