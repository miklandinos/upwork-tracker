"""Точка входа: весь цикл §1 ТЗ. Запуск: python -m src.main

Идемпотентность:
  * дедуп по Upwork ID — повторный прогон не создаёт дублей;
  * пуши шлются только по записям с Notified=false и помечаются после отправки —
    повторный прогон не шлёт повторных уведомлений, а хвосты упавших прогонов дошлёт.

Один сбойный проект / сбой Apify не роняет весь запуск: ошибки логируются,
шаги уведомлений и очистки выполняются в любом случае.
"""
from __future__ import annotations

import logging
import time as time_module
from datetime import datetime, time
from zoneinfo import ZoneInfo

import anthropic

from . import config
from .airtable_client import AirtableClient
from .apify_client import fetch_jobs
from .classifier import CATEGORY_MAP, classify
from .filters import passes_keyword_filter
from .telegram import send_job_notification, send_status_message

log = logging.getLogger(__name__)

QUERY_SLOT_SECONDS = 1800  # 30-минутный слот cron — шаг round-robin по запросам


def parse_hhmm(value: str, fallback: str) -> time:
    for candidate in (value, fallback):
        try:
            return datetime.strptime(candidate.strip(), "%H:%M").time()
        except (ValueError, AttributeError):
            continue
    return time(0, 0)


def is_within_window(now: time, start: time, end: time) -> bool:
    """Активно ли рабочее окно. Окно 11:00→02:00 переходит через полночь (§3.1 ТЗ):
    start < end  → обычное окно: start <= now <= end
    start > end  → через полночь: now >= start OR now <= end
    start == end → окно 24 часа
    """
    if start == end:
        return True
    if start < end:
        return start <= now <= end
    return now >= start or now <= end


def pick_query(queries: list[str], now_ts: float | None = None) -> str:
    """Round-robin без хранения состояния: индекс = номер 30-мин слота (§5 ТЗ).

    GitHub Actions stateless, поэтому курсор выводится из времени —
    каждый следующий 30-минутный прогон берёт следующий запрос по кругу.
    """
    if now_ts is None:
        now_ts = time_module.time()
    return queries[int(now_ts // QUERY_SLOT_SECONDS) % len(queries)]


def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    airtable = AirtableClient(
        config.AIRTABLE_TOKEN,
        config.AIRTABLE_BASE_ID,
        config.AIRTABLE_TABLE,
        config.AIRTABLE_SETTINGS_TABLE,
    )

    # 1. Настройки из Airtable (при недоступности — дефолты из config.py)
    settings = airtable.load_settings(config.DEFAULT_SETTINGS)

    # 2. Enabled + рабочее окно: вне окна выходим, НЕ трогая Apify и AI (§3.1)
    if not settings["enabled"]:
        log.info("Enabled=false в Settings — выходим без запросов")
        return 0

    tz_name = settings["timezone"]
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        log.warning("Неизвестная таймзона %r, использую Asia/Bangkok", tz_name)
        tz = ZoneInfo("Asia/Bangkok")
    now_local = datetime.now(tz)
    start = parse_hhmm(settings["active_start"], config.DEFAULT_SETTINGS["active_start"])
    end = parse_hhmm(settings["active_end"], config.DEFAULT_SETTINGS["active_end"])
    if not is_within_window(now_local.time(), start, end):
        log.info(
            "Сейчас %s (%s) — вне рабочего окна %s–%s, выходим без запросов",
            now_local.strftime("%H:%M"), tz_name, settings["active_start"], settings["active_end"],
        )
        return 0

    # 3. Apify: один запрос за цикл, round-robin по Search Queries
    query = pick_query(settings["search_queries"])
    jobs: list[dict] = []
    apify_ok = True
    try:
        jobs = fetch_jobs(config.APIFY_TOKEN, config.APIFY_ACTOR_ID, query, rows=config.APIFY_ROWS)
    except Exception:
        apify_ok = False
        log.error("Сбой Apify — пропускаю приём вакансий, продолжаю прогон", exc_info=True)

    # 4-6. Грубый фильтр → дедуп → Haiku → запись
    stats = {"fetched": len(jobs), "filtered": 0, "duplicates": 0, "classified": 0,
             "relevant": 0, "errors": 0}
    if jobs:
        existing_ids = airtable.existing_upwork_ids()
        ai_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)
        keywords = settings["keywords"]
        for job in jobs:
            try:
                if job["upwork_id"] in existing_ids:
                    stats["duplicates"] += 1
                    continue
                if not passes_keyword_filter(job.get("title"), job.get("description"), keywords):
                    stats["filtered"] += 1
                    log.info("Отсечено грубым фильтром: %r", job.get("title"))
                    continue
                ai = classify(job.get("title"), job.get("description"), client=ai_client)
                if ai is None:
                    stats["errors"] += 1
                    continue
                stats["classified"] += 1
                if not ai["relevant"]:
                    log.info("AI: не релевантно (%s): %r", ai["reason"], job.get("title"))
                    continue
                category_ru = CATEGORY_MAP[ai["category"]]
                airtable.create_project(job, ai, category_ru)
                existing_ids.add(job["upwork_id"])
                stats["relevant"] += 1
                log.info("Добавлено в Airtable [%s]: %r", category_ru, job.get("title"))
            except Exception:
                stats["errors"] += 1
                log.error("Сбой обработки вакансии %r — пропускаю", job.get("title"), exc_info=True)

    # 7. Telegram: только Notified=false, метим после успешной отправки
    notified = 0
    try:
        for record in airtable.unnotified_records():
            fields = record.get("fields", {})
            if send_job_notification(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, fields):
                airtable.mark_notified(record["id"])
                notified += 1
    except Exception:
        log.error("Сбой шага уведомлений — продолжаю прогон", exc_info=True)

    # 8. Очистка старше Retention Days (идемпотентно, каждый прогон)
    deleted = 0
    try:
        deleted = airtable.delete_older_than(settings["retention_days"])
    except Exception:
        log.error("Сбой очистки старых записей", exc_info=True)

    log.info(
        "Готово: получено %(fetched)d, дублей %(duplicates)d, отсечено фильтром %(filtered)d, "
        "классифицировано %(classified)d, добавлено %(relevant)d, ошибок %(errors)d; "
        "пушей %(notified)d, удалено старых %(deleted)d",
        {**stats, "notified": notified, "deleted": deleted},
    )

    # 9. Heartbeat: отметка о скане в Settings и тихое статус-сообщение в Telegram
    if apify_ok:
        result_text = (
            f"получено {stats['fetched']}, новых {stats['relevant']}"
            + (f", ошибок {stats['errors']}" if stats["errors"] else "")
        )
        status_line = (
            f"🔄 Скан Upwork ({query}): {result_text}"
            if stats["relevant"] else
            f"🔄 Скан Upwork ({query}): {result_text} — новых проектов нет"
        )
    else:
        result_text = "ошибка Apify — вакансии не получены"
        status_line = f"⚠️ Скан Upwork ({query}): {result_text}"
    try:
        airtable.record_scan_status(now_local.isoformat(), result_text)
    except Exception:
        log.error("Не удалось записать статус скана в Settings", exc_info=True)
    send_status_message(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, status_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
