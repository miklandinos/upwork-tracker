"""Запуск Apify-актора и нормализация вакансий (§5 ТЗ).

Поддерживаются оба актора:
  * flash_mage/upwork        — основной, вход {"keyword", "sort", "rows"}
  * neatrat/upwork-job-scraper — запасной, вход {"query", "sort", "maxJobAge"}

Переключение — через APIFY_ACTOR_ID. Выходные схемы у акторов различаются,
поэтому normalize_job() терпим к разным именам полей.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import requests

log = logging.getLogger(__name__)

APIFY_RUN_SYNC_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
REQUEST_TIMEOUT = 300  # актор может работать до нескольких минут


def _actor_url_id(actor_id: str) -> str:
    """user/actor -> user~actor (формат пути Apify API); raw id оставляем как есть."""
    return actor_id.replace("/", "~")


def _is_neatrat(actor_id: str) -> bool:
    return "neatrat" in actor_id or actor_id == "XYTgO05GT5qAoSlxy"


def build_actor_input(actor_id: str, query: str, rows: int = 5) -> dict:
    """Тело запуска актора. Форматы входа у акторов разные (§5 / §5.1 ТЗ)."""
    if _is_neatrat(actor_id):
        # neatrat/upwork-job-scraper: query + maxJobAge (часы) + sort
        return {"query": query, "sort": "recency", "maxJobAge": 24}
    # flash_mage/upwork — схема сверена по input-schema актора (2026-07):
    # query — массив ключевых слов (до 5), limit — 5..500, sort — relevance|newest
    return {"query": [query], "sort": "newest", "limit": rows}


def fetch_jobs(token: str, actor_id: str, query: str, rows: int = 5) -> list[dict]:
    """Один синхронный запуск актора → список нормализованных вакансий.

    Ошибки сети/актора пробрасываются наверх — main.py логирует и
    продолжает прогон (уведомления/очистка не должны падать из-за Apify).
    """
    url = APIFY_RUN_SYNC_URL.format(actor=_actor_url_id(actor_id))
    payload = build_actor_input(actor_id, query, rows)
    log.info("Apify: запуск актора %s, запрос %r", actor_id, query)
    resp = requests.post(
        url,
        params={"token": token},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        raise ValueError(f"Apify вернул не список: {type(items).__name__}")
    jobs = []
    for item in items:
        try:
            job = normalize_job(item)
        except Exception:
            log.warning("Не удалось нормализовать вакансию, пропускаю: %r", item, exc_info=True)
            continue
        if job.get("upwork_id"):
            jobs.append(job)
        else:
            log.warning("Вакансия без id, пропускаю: %r", item.get("title"))
    log.info("Apify: получено %d вакансий", len(jobs))
    return jobs


# --- нормализация ---------------------------------------------------------

def _first(item: dict, *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _nested(item: dict, *path: str) -> Any:
    node: Any = item
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if node not in (None, "") else None


def _money(value: Any) -> str | None:
    """Число / строка / {"amount": ..} → строка с суммой."""
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        amount = value.get("amount")
        currency = value.get("currencyCode") or ""
        if amount in (None, ""):
            return None
        return f"{_money(amount)}{' ' + currency if currency and currency != 'USD' else ''}".strip()
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _extract_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"~(0[0-9a-zA-Z]+)", url)
    if match:
        return match.group(1)
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def _budget_string(item: dict) -> str | None:
    fixed = _money(_first(item, "fixedPriceAmount", "budget", "amount"))
    hourly_min = _money(_first(item, "hourlyBudgetMin", "hourlyMin", "minHourlyRate"))
    hourly_max = _money(_first(item, "hourlyBudgetMax", "hourlyMax", "maxHourlyRate"))
    if hourly_min or hourly_max:
        if hourly_min and hourly_max:
            return f"${hourly_min}–${hourly_max}/hr"
        return f"${hourly_min or hourly_max}/hr"
    if fixed:
        return f"${fixed} (fixed)"
    return None


def _job_type(item: dict) -> str | None:
    raw = _first(item, "jobType", "type", "engagementType", "engagement")
    if not raw:
        if _first(item, "hourlyBudgetMin", "hourlyBudgetMax"):
            return "Hourly"
        if _first(item, "fixedPriceAmount"):
            return "Fixed"
        return None
    raw_l = str(raw).lower()
    if "hour" in raw_l:
        return "Hourly"
    if "fix" in raw_l:
        return "Fixed"
    return None


def normalize_job(item: dict) -> dict:
    """Единый объект вакансии (§5 ТЗ). Отсутствующие значения — None."""
    url = _first(item, "link", "url", "jobUrl", "jobLink")
    upwork_id = _first(item, "id", "jobId", "uid", "ciphertext") or _extract_id_from_url(url)
    return {
        "upwork_id": str(upwork_id) if upwork_id else None,
        "title": _first(item, "title", "jobTitle"),
        "url": url,
        "description": _first(item, "description", "descriptionText", "snippet"),
        "budget": _budget_string(item),
        "job_type": _job_type(item),
        "country": (
            _nested(item, "buyer", "location", "country")
            or _first(item, "country", "clientCountry", "clientLocation")
        ),
        "client_spent": (
            _money(_nested(item, "buyer", "stats", "totalCharges"))
            or _money(_first(item, "clientTotalSpent", "totalSpent"))
        ),
        "posted_at": _first(
            item, "publishTime", "createTime", "publishedOn", "createdOn", "postedOn", "publishedDate"
        ),
    }
