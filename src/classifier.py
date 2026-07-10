"""Классификатор вакансий на Claude Haiku 4.5 (§4.2 ТЗ).

Системный промпт и few-shot примеры зашиты из ТЗ дословно.
Few-shot передаются как настоящие user/assistant-пары в messages —
это надёжнее удерживает модель в формате строгого JSON.
"""
from __future__ import annotations

import json
import logging
import re

import anthropic

from . import config

log = logging.getLogger(__name__)

VALID_CATEGORIES = {"real_estate", "gamedev", "other", "web_interactive_realestate"}

# Маппинг category → поле Airtable `Category` (§4.2 ТЗ)
CATEGORY_MAP = {
    "real_estate": "Недвижимость",
    "gamedev": "Геймдев",
    "other": "Другие сферы",
    "web_interactive_realestate": "Веб-интерактив",
}

SYSTEM_PROMPT = """Ты — фильтр вакансий с Upwork для студии, которая делает интерактивные 3D-решения
на Unreal Engine (UE5/UE4). Твоя задача: решить, подходит ли вакансия, и определить категорию.

ВКЛЮЧАЙ (relevant=true), если проект — это:
- разработка на Unreal Engine (UE5/UE4) в любой сфере;
- ИЛИ интерактивное веб-решение для недвижимости (pixel streaming, WebGL, three.js,
  браузерные конфигураторы/интерактивные планировки), даже если Unreal явно не назван.

ИСКЛЮЧАЙ (relevant=false):
- чистый веб/мобайл без 3D;
- монтаж видео, 2D-графика, только моделинг в Blender/3ds Max без интерактива и без UE;
- геймдев на Unity и других движках (кроме Unreal);
- всё, не связанное с реал-тайм 3D.

КАТЕГОРИИ:
- real_estate: UE для недвижимости (archviz, виртуальные туры, презентации ЖК, конфигураторы квартир).
- gamedev: UE для игр (геймплей, мультиплеер, прототипы, порты).
- other: UE для остального (product viz, virtual production, симуляции, обучение, авто, кино).
- web_interactive_realestate: интерактив для недвижимости под веб (pixel streaming, WebGL, three.js,
  браузерные конфигураторы и интерактивные карты объектов).

Отвечай ТОЛЬКО валидным JSON по схеме:
{"relevant": true|false, "category": "real_estate|gamedev|other|web_interactive_realestate",
 "confidence": 0.0-1.0, "reason": "1-2 предложения", "summary_ru": "краткая суть на русском, 1-2 предложения"}
Без markdown, без комментариев."""

# Few-shot примеры из §4.2 ТЗ: 2 положительных + 3 отрицательных
FEW_SHOT_EXAMPLES: list[tuple[str, dict]] = [
    (
        "Title: Unreal Engine Artist Needed to Build Realistic 3D Jeddah City From Scratch\n"
        "Description: We need an experienced Unreal Engine artist or team to build a realistic 3D "
        "Jeddah city from scratch. The project involves creating a detailed, immersive environment "
        "that accurately represents the city's architecture, streets, and surroundings. This is a "
        "long-term opportunity for someone who can deliver high-quality, visually compelling work "
        "and collaborate effectively throughout the development process.\n"
        "(Hourly, 6+ months, Intermediate, Mandatory skill: Unreal Engine. Client: Saudi Arabia, "
        "$57K spent, 9 hires.)",
        {
            "relevant": True,
            "category": "other",
            "confidence": 0.9,
            "reason": "Разработка реалистичного 3D-города на Unreal Engine — реал-тайм 3D-окружение, "
                      "но не про продажу недвижимости и не игра.",
            "summary_ru": "Нужен UE-художник/команда, чтобы с нуля построить реалистичный 3D-город "
                          "Джидда (архитектура, улицы, окружение). Долгосрочно.",
        },
    ),
    (
        "Title: Unreal Engine Developer for Interactive Content\n"
        "Description: We are a technology company building a sales demo for off-plan real estate "
        "developers: a photoreal, walkable luxury apartment in UE 5.5+ where a buyer walks up to the "
        "kitchen, bathroom, or floor area, a branded widget appears, and choosing an upgrade option "
        "(e.g., three kitchen packages, four floor finishes) swaps the model in real time with a "
        "running price total. Data-driven customization system in Blueprints (proximity-triggered "
        "widgets, UMG panels, instant material swaps, AED price ticker, save-to-JSON). PCVR support "
        "(Quest 3 via Link, OpenXR). Packaged Windows build (60 fps @1440p). 8K 360 panoramas + cinematic. "
        "Must have: photoreal UE5 interior archviz (Lumen), real Blueprint experience (DataTables, actor "
        "components, event dispatchers, UMG), runtime material/mesh swapping (configurators, variant systems).",
        {
            "relevant": True,
            "category": "real_estate",
            "confidence": 0.98,
            "reason": "Интерактивная фотореалистичная квартира на UE5.5 с конфигуратором отделки и "
                      "ценником — продающий инструмент для застройщиков недвижимости.",
            "summary_ru": "Sales-demo для застройщиков: ходибельная фотореалистичная квартира на UE5.5 "
                          "с конфигуратором отделки в реальном времени и подсчётом цены. Blueprints, "
                          "PCVR, рендер панорам.",
        },
    ),
    (
        "Title: Unity Mobile Game Developer for Casual Puzzle Game\nDescription: ",
        {
            "relevant": False,
            "category": "gamedev",
            "confidence": 0.95,
            "reason": "Движок Unity, не Unreal — вне профиля.",
            "summary_ru": "Разработка мобильной казуальной игры на Unity.",
        },
    ),
    (
        "Title: Edit My YouTube Videos and Add Motion Graphics\nDescription: ",
        {
            "relevant": False,
            "category": "other",
            "confidence": 0.97,
            "reason": "Монтаж видео и 2D-графика, нет реал-тайм 3D / Unreal.",
            "summary_ru": "Монтаж роликов для YouTube и моушн-графика.",
        },
    ),
    (
        "Title: 3D Product Modeling in Blender for E-commerce\nDescription: ",
        {
            "relevant": False,
            "category": "other",
            "confidence": 0.9,
            "reason": "Только моделинг в Blender, без интерактива и без Unreal.",
            "summary_ru": "Моделинг товаров в Blender для интернет-магазина.",
        },
    ),
]


def _few_shot_messages() -> list[dict]:
    messages = []
    for user_text, expected in FEW_SHOT_EXAMPLES:
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": json.dumps(expected, ensure_ascii=False)})
    return messages


def parse_classifier_json(text: str) -> dict:
    """Строгий разбор ответа модели: срезаем фенсы/обвязку, валидируем поля.

    Бросает ValueError при любом отклонении от схемы —
    вызывающий код логирует и пропускает вакансию.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"В ответе модели нет JSON-объекта: {text!r}")
    data = json.loads(cleaned[start : end + 1])

    if not isinstance(data.get("relevant"), bool):
        raise ValueError(f"Поле relevant отсутствует или не bool: {data!r}")
    category = data.get("category")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Недопустимая категория: {category!r}")
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        raise ValueError(f"Поле confidence не число: {data.get('confidence')!r}")
    confidence = max(0.0, min(1.0, confidence))

    return {
        "relevant": data["relevant"],
        "category": category,
        "confidence": confidence,
        "reason": str(data.get("reason") or ""),
        "summary_ru": str(data.get("summary_ru") or ""),
    }


def classify(title: str | None, description: str | None, *, client: anthropic.Anthropic | None = None) -> dict | None:
    """Классификация одной вакансии. None при любой ошибке (лог + продолжаем прогон)."""
    if client is None:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)
    user_text = f"Title: {title or ''}\nDescription: {description or ''}"
    try:
        response = client.messages.create(
            model=config.CLASSIFIER_MODEL,
            max_tokens=config.CLASSIFIER_MAX_TOKENS,
            temperature=config.CLASSIFIER_TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=_few_shot_messages() + [{"role": "user", "content": user_text}],
        )
    except anthropic.APIError:
        log.error("Ошибка Anthropic API при классификации %r", title, exc_info=True)
        return None
    except Exception:
        log.error("Неожиданная ошибка при классификации %r", title, exc_info=True)
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return parse_classifier_json(text)
    except (ValueError, json.JSONDecodeError):
        log.error("Невалидный JSON от классификатора для %r: %r", title, text, exc_info=True)
        return None
