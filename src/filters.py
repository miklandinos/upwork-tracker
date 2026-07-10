"""Грубый фильтр по ключевым словам (§4.1 ТЗ) — до AI, экономит токены."""
from __future__ import annotations


def passes_keyword_filter(title: str | None, description: str | None, keywords: list[str]) -> bool:
    """True, если в Title ИЛИ Description встречается хотя бы одно ключевое слово.

    Регистр игнорируется. Пустой список ключей пропускает всё
    (фильтр фактически выключен через Settings).
    """
    if not keywords:
        return True
    haystack = f"{title or ''} {description or ''}".lower()
    return any(kw.strip().lower() in haystack for kw in keywords if kw and kw.strip())
