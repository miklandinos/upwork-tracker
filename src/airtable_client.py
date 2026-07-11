"""Airtable: настройки, дедуп, запись, флаг Notified, retention-очистка (§3, §3.1 ТЗ)."""
from __future__ import annotations

import logging

from pyairtable import Api

log = logging.getLogger(__name__)


class AirtableClient:
    def __init__(self, token: str, base_id: str, table_name: str, settings_table_name: str):
        api = Api(token)
        self.projects = api.table(base_id, table_name)
        self.settings_table = api.table(base_id, settings_table_name)
        self._settings_record_id: str | None = None  # запоминается при load_settings

    # --- Settings (§3.1) ---------------------------------------------------

    def load_settings(self, defaults: dict) -> dict:
        """Первая запись таблицы Settings поверх дефолтов из config.py.

        Если таблица недоступна — работаем на дефолтах (мягкая деградация).
        """
        merged = dict(defaults)
        try:
            records = self.settings_table.all(max_records=1)
        except Exception:
            log.warning("Таблица Settings недоступна — использую дефолты из config.py", exc_info=True)
            return merged
        if not records:
            log.warning("Таблица Settings пуста — использую дефолты из config.py")
            return merged

        self._settings_record_id = records[0]["id"]
        fields = records[0].get("fields", {})
        # Checkbox: Airtable не возвращает поле, если галочка снята → False
        merged["enabled"] = bool(fields.get("Enabled", False))

        queries = fields.get("Search Queries")
        if isinstance(queries, str):
            parsed = [line.strip() for line in queries.splitlines() if line.strip()]
            if parsed:
                merged["search_queries"] = parsed

        keywords = fields.get("Keyword Filter")
        if isinstance(keywords, str):
            parsed = [kw.strip() for kw in keywords.split(",") if kw.strip()]
            if parsed:
                merged["keywords"] = parsed

        for field, key in (("Active Start", "active_start"), ("Active End", "active_end"), ("Timezone", "timezone")):
            value = fields.get(field)
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip()

        retention = fields.get("Retention Days")
        if isinstance(retention, (int, float)) and retention > 0:
            merged["retention_days"] = int(retention)

        return merged

    def record_scan_status(self, scanned_at_iso: str, result_text: str) -> None:
        """Отметка о последнем скане в строке Settings (Last Scan At / Last Scan Result)."""
        if not self._settings_record_id:
            log.warning("Нет записи Settings — статус скана не записан")
            return
        self.settings_table.update(
            self._settings_record_id,
            {"Last Scan At": scanned_at_iso, "Last Scan Result": result_text},
            typecast=True,
        )

    # --- Projects (§3) -----------------------------------------------------

    def existing_upwork_ids(self) -> set[str]:
        """Все Upwork ID в базе — ключ дедупликации. Retention держит таблицу маленькой."""
        ids: set[str] = set()
        for record in self.projects.all(fields=["Upwork ID"]):
            value = record.get("fields", {}).get("Upwork ID")
            if value:
                ids.add(str(value))
        return ids

    def create_project(self, job: dict, ai: dict, category_ru: str) -> dict:
        """Новая запись со Status=Новый и Notified=False (пуш отправит шаг уведомлений)."""
        fields = {
            "Upwork ID": job["upwork_id"],
            "Title": job.get("title"),
            "URL": job.get("url"),
            "Category": category_ru,
            "Summary RU": ai.get("summary_ru"),
            "Budget": job.get("budget"),
            "Job Type": job.get("job_type"),
            "Country": job.get("country"),
            "Client Spent": job.get("client_spent"),
            "Posted At": job.get("posted_at"),
            "Bids": job.get("bids"),
            "Status": "Новый",
            "AI Confidence": ai.get("confidence"),
            "AI Reason": ai.get("reason"),
            "Notified": False,
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        # typecast=True: Airtable сам создаст недостающие опции single select
        return self.projects.create(fields, typecast=True)

    def unnotified_records(self) -> list[dict]:
        """Записи, по которым ещё не было пуша (включая хвосты упавших прогонов)."""
        formula = "AND(NOT({Notified}), {Status} != 'Удалён')"
        return self.projects.all(formula=formula)

    def mark_notified(self, record_id: str) -> None:
        self.projects.update(record_id, {"Notified": True})

    def delete_older_than(self, days: int) -> int:
        """Удалить записи старше retention. Идемпотентно, дёшево — можно звать каждый прогон."""
        formula = f"IS_BEFORE(CREATED_TIME(), DATEADD(NOW(), -{int(days)}, 'days'))"
        old = self.projects.all(formula=formula)
        if old:
            self.projects.batch_delete([record["id"] for record in old])
        return len(old)
