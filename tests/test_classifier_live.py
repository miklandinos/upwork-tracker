"""Живой тест классификатора (§10 п.4 ТЗ): 2 эталонных + 3 отрицательных примера из §4.2.

Требует ANTHROPIC_API_KEY — без него тест помечается skipped.
Запуск: ANTHROPIC_API_KEY=... pytest tests/test_classifier_live.py -v
"""
import os

import pytest

from src.classifier import classify

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="нужен ANTHROPIC_API_KEY для живого теста классификатора",
)

# Эталонные вакансии из §4.2 (тексты те же, что в few-shot, но few-shot и должен
# закреплять именно эти ответы; temperature=0 делает результат воспроизводимым)
CASES = [
    # (title, description, ожидаемый relevant, ожидаемая категория)
    (
        "Unreal Engine Artist Needed to Build Realistic 3D Jeddah City From Scratch",
        "We need an experienced Unreal Engine artist or team to build a realistic 3D Jeddah city "
        "from scratch. The project involves creating a detailed, immersive environment that "
        "accurately represents the city's architecture, streets, and surroundings. This is a "
        "long-term opportunity. Hourly, 6+ months, Mandatory skill: Unreal Engine.",
        True,
        "other",
    ),
    (
        "Unreal Engine Developer for Interactive Content",
        "We are building a sales demo for off-plan real estate developers: a photoreal, walkable "
        "luxury apartment in UE 5.5+ where a buyer chooses upgrade options (kitchen packages, "
        "floor finishes) and the model swaps in real time with a running price total. Data-driven "
        "customization in Blueprints, UMG, runtime material swapping, PCVR support, 8K panoramas. "
        "Must have photoreal UE5 interior archviz (Lumen) experience.",
        True,
        "real_estate",
    ),
    (
        "Unity Mobile Game Developer for Casual Puzzle Game",
        "Looking for a Unity developer to build a casual mobile puzzle game for iOS and Android.",
        False,
        None,  # категория при relevant=false не принципиальна
    ),
    (
        "Edit My YouTube Videos and Add Motion Graphics",
        "Need a video editor for weekly YouTube videos with motion graphics and subtitles.",
        False,
        None,
    ),
    (
        "3D Product Modeling in Blender for E-commerce",
        "Model our product catalog in Blender for use in online store product pages.",
        False,
        None,
    ),
]


@pytest.mark.parametrize("title,description,expected_relevant,expected_category", CASES)
def test_classifier_reference_cases(title, description, expected_relevant, expected_category):
    result = classify(title, description)
    assert result is not None, "классификатор вернул None (ошибка API или невалидный JSON)"
    assert result["relevant"] is expected_relevant, f"relevant: ожидали {expected_relevant}, получили {result}"
    if expected_relevant and expected_category:
        assert result["category"] == expected_category, f"category: ожидали {expected_category}, получили {result}"
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["summary_ru"], "summary_ru не должен быть пустым"
