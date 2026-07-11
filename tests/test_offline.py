"""Юнит-тесты без сети: окно через полночь, фильтр, парсинг JSON, round-robin, нормализация."""
from datetime import time

import pytest

from src.apify_client import build_actor_input, normalize_job
from src.classifier import CATEGORY_MAP, FEW_SHOT_EXAMPLES, parse_classifier_json
from src.config import DEFAULT_KEYWORDS
from src.filters import passes_keyword_filter
from src.main import is_within_window, parse_hhmm, pick_query


# --- рабочее окно (§3.1: 11:00→02:00 переходит через полночь) --------------

@pytest.mark.parametrize(
    "now,expected",
    [
        (time(11, 0), True),    # начало окна
        (time(15, 30), True),   # день
        (time(23, 59), True),   # поздний вечер
        (time(0, 30), True),    # после полуночи
        (time(2, 0), True),     # конец окна
        (time(2, 1), False),    # сразу после окна
        (time(5, 0), False),    # ночь
        (time(10, 59), False),  # перед окном
    ],
)
def test_overnight_window(now, expected):
    assert is_within_window(now, time(11, 0), time(2, 0)) is expected


def test_regular_window():
    assert is_within_window(time(12, 0), time(9, 0), time(18, 0)) is True
    assert is_within_window(time(8, 59), time(9, 0), time(18, 0)) is False
    assert is_within_window(time(18, 1), time(9, 0), time(18, 0)) is False


def test_equal_bounds_means_24h():
    assert is_within_window(time(3, 33), time(11, 0), time(11, 0)) is True


def test_parse_hhmm_fallback():
    assert parse_hhmm("11:00", "00:00") == time(11, 0)
    assert parse_hhmm("garbage", "02:00") == time(2, 0)


# --- грубый фильтр (§4.1) ---------------------------------------------------

def test_filter_passes_ue_jobs():
    assert passes_keyword_filter("Unreal Engine Developer", None, DEFAULT_KEYWORDS)
    assert passes_keyword_filter("Need help", "photoreal ARCHVIZ scene", DEFAULT_KEYWORDS)
    assert passes_keyword_filter("3D Tour for apartments", "", DEFAULT_KEYWORDS)


def test_filter_rejects_noise():
    assert not passes_keyword_filter("Edit My YouTube Videos", "motion graphics", DEFAULT_KEYWORDS)
    assert not passes_keyword_filter("Shopify store setup", "web design", DEFAULT_KEYWORDS)


def test_filter_empty_keywords_passes_everything():
    assert passes_keyword_filter("anything", "at all", [])


# --- парсинг JSON от Haiku (§4.2) --------------------------------------------

VALID_JSON = (
    '{"relevant": true, "category": "real_estate", "confidence": 0.9,'
    ' "reason": "ok", "summary_ru": "суть"}'
)


def test_parse_plain_json():
    result = parse_classifier_json(VALID_JSON)
    assert result["relevant"] is True
    assert result["category"] == "real_estate"
    assert result["confidence"] == 0.9


def test_parse_fenced_json():
    result = parse_classifier_json(f"```json\n{VALID_JSON}\n```")
    assert result["category"] == "real_estate"


def test_parse_json_with_surrounding_prose():
    result = parse_classifier_json(f"Вот ответ:\n{VALID_JSON}\nГотово.")
    assert result["relevant"] is True


def test_parse_clamps_confidence():
    result = parse_classifier_json(VALID_JSON.replace("0.9", "1.7"))
    assert result["confidence"] == 1.0


def test_parse_rejects_bad_category():
    with pytest.raises(ValueError):
        parse_classifier_json(VALID_JSON.replace("real_estate", "unknown_cat"))


def test_parse_rejects_no_json():
    with pytest.raises(ValueError):
        parse_classifier_json("извините, не могу")


def test_few_shot_examples_intact():
    """В промпт зашиты все 5 примеров из §4.2: 2 положительных + 3 отрицательных."""
    positives = [ex for _, ex in FEW_SHOT_EXAMPLES if ex["relevant"]]
    negatives = [ex for _, ex in FEW_SHOT_EXAMPLES if not ex["relevant"]]
    assert len(positives) == 2
    assert len(negatives) == 3


def test_category_map_covers_all():
    assert set(CATEGORY_MAP) == {"real_estate", "gamedev", "other", "web_interactive_realestate"}
    assert CATEGORY_MAP["web_interactive_realestate"] == "Веб-интерактив"


# --- round-robin запросов без состояния (§5) ---------------------------------

def test_pick_query_round_robin():
    queries = ["Unreal Engine", "pixel streaming", "archviz"]
    assert pick_query(queries, now_ts=0) == "Unreal Engine"
    assert pick_query(queries, now_ts=1800) == "pixel streaming"
    assert pick_query(queries, now_ts=3600) == "archviz"
    assert pick_query(queries, now_ts=5400) == "Unreal Engine"


def test_pick_query_single():
    assert pick_query(["Unreal Engine"], now_ts=123456789) == "Unreal Engine"


# --- нормализация выхода Apify (§5) -------------------------------------------

def test_normalize_flash_mage_shape():
    """Реальная структура flash_mage/upwork: данные вложены в data.opening.*,
    верхнеуровневый id — порядковый номер строки (НЕ id вакансии)."""
    job = normalize_job({
        "id": "0",  # индекс строки — должен игнорироваться
        "title": "UE5 Archviz",
        "link": "https://www.upwork.com/jobs/~021234567890",
        "data": {
            "opening": {
                "description": "Build a virtual tour",
                "publishTime": "2026-07-10T10:00:00Z",
                "info": {"ciphertext": "~021234567890", "id": "1234567890", "type": "HOURLY"},
                "budget": {"amount": 0, "currencyCode": "USD"},
                "extendedBudgetInfo": {"hourlyBudgetMin": 30, "hourlyBudgetMax": 60},
                "clientActivity": {"totalApplicants": 27},
            },
            "buyer": {
                "location": {"country": "United States"},
                "stats": {"totalCharges": {"amount": 5000, "currencyCode": "USD"}},
            },
        },
    })
    assert job["upwork_id"] == "021234567890"  # без тильды, не индекс строки
    assert job["job_type"] == "Hourly"
    assert job["budget"] == "$30–$60/hr"
    assert job["country"] == "United States"
    assert job["client_spent"] == "5000"
    assert job["posted_at"] == "2026-07-10T10:00:00Z"
    assert job["bids"] == 27
    assert job["description"] == "Build a virtual tour"


def test_normalize_flash_mage_fixed_price():
    job = normalize_job({
        "id": "1",
        "title": "Fixed job",
        "link": "https://www.upwork.com/jobs/~02777",
        "data": {
            "opening": {
                "info": {"ciphertext": "~02777", "type": "FIXED"},
                "budget": {"amount": 500, "currencyCode": "USD"},
            },
        },
    })
    assert job["job_type"] == "Fixed"
    assert job["budget"] == "$500 (fixed)"
    assert job["bids"] is None


def test_normalize_neatrat_shape_and_id_from_url():
    job = normalize_job({
        "title": "Unreal Developer",
        "url": "https://www.upwork.com/jobs/Unreal-Developer_~02abcdef123",
        "description": "gameplay",
        "type": "Fixed-price",
        "budget": 500,
        "clientCountry": "Germany",
        "createdOn": "2026-07-10T09:00:00Z",
    })
    assert job["upwork_id"] == "02abcdef123"
    assert job["job_type"] == "Fixed"
    assert job["budget"] == "$500 (fixed)"
    assert job["country"] == "Germany"


def test_normalize_missing_values_are_none():
    job = normalize_job({"id": "x1", "title": "t"})
    assert job["budget"] is None
    assert job["country"] is None
    assert job["posted_at"] is None


# --- вход акторов (§5 / §5.1) -------------------------------------------------

def test_build_input_flash_mage():
    assert build_actor_input("flash_mage/upwork", "Unreal Engine", rows=5) == {
        "query": ["Unreal Engine"],
        "sort": "newest",
        "limit": 5,
    }


def test_build_input_neatrat():
    body = build_actor_input("neatrat/upwork-job-scraper", "Unreal Engine")
    assert body["query"] == "Unreal Engine"
    assert "keyword" not in body
