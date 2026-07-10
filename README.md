# Upwork 3D Tracker

Трекер UE5-вакансий с Upwork: каждые 30 минут забирает свежие вакансии через **Apify**, классифицирует их через **Claude Haiku 4.5**, складывает в **Airtable** (он же интерфейс трекера) и шлёт пуш в **Telegram**-группу. Планировщик — **GitHub Actions**, постоянного сервера нет.

```
GitHub Actions (cron */30)
  → Settings из Airtable (Enabled, рабочее окно 11:00–02:00 Asia/Bangkok, критерии)
  → Apify (5 свежих вакансий, round-robin по поисковым запросам)
  → грубый фильтр по ключевым словам → дедуп по Upwork ID
  → Claude Haiku 4.5 → relevant? + категория + summary на русском
  → запись в Airtable (Status=Новый) → пуш в Telegram → Notified=true
  → очистка записей старше Retention Days
```

## Структура

```
src/
  main.py            # точка входа: весь цикл
  apify_client.py    # запуск актора (flash_mage/upwork | neatrat), нормализация
  classifier.py      # промпт + few-shot + вызов Haiku 4.5 (temperature=0)
  airtable_client.py # settings, дедуп, запись, Notified, очистка
  telegram.py        # пуши в группу
  config.py          # env + дефолты
  filters.py         # грубый фильтр по ключам
bootstrap_airtable.py # разовое создание таблиц Projects и Settings
.github/workflows/scan.yml
tests/               # офлайн-тесты + живой тест классификатора
```

## Шаг 1. Получение ключей

### Telegram-бот
1. В Telegram открыть **@BotFather** → `/newbot` → задать имя и username → скопировать **токен** → `TELEGRAM_BOT_TOKEN`.
2. Создать **групповой чат**, добавить в него бота и всех сотрудников.
3. Добавить в группу @RawDataBot (или @getidsbot) — он покажет **chat_id группы** (число с минусом, например `-1001234567890`) → `TELEGRAM_CHAT_ID`. После этого сервисного бота можно удалить из группы.

### Apify
1. Регистрация на [apify.com](https://apify.com) → Console → **Settings → Integrations → API tokens** → скопировать токен → `APIFY_TOKEN`.
2. Основной актор — [`flash_mage/upwork`](https://apify.com/flash_mage/upwork) (оплата за результат, ~$0.001/вакансия). На его странице открыть вкладку **Input** и сверить имена параметров (`keyword`, `sort`, `rows`) — если актор обновился, поправить `build_actor_input()` в [apify_client.py](src/apify_client.py).
3. Запасной актор: `neatrat/upwork-job-scraper` — переключение через секрет `APIFY_ACTOR_ID`, код поддерживает оба формата входа/выхода.

### Anthropic API (Haiku)
1. [console.anthropic.com](https://console.anthropic.com) → привязать оплату (Billing) → **API Keys → Create Key** → `ANTHROPIC_API_KEY`.

### Airtable
1. [airtable.com](https://airtable.com) → Create → **Start from scratch** → создать **пустую** базу (таблицы создаст `bootstrap_airtable.py`).
2. **Base ID**: открыть базу → в URL часть вида `appXXXXXXXXXXXXXX` → `AIRTABLE_BASE_ID`.
3. **Token**: [airtable.com/create/tokens](https://airtable.com/create/tokens) → Create token → scopes: `data.records:read`, `data.records:write`, `schema.bases:read`, `schema.bases:write` → в Access добавить свою базу → `AIRTABLE_TOKEN`.

## Шаг 2. Создание таблиц Airtable

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # заполнить AIRTABLE_TOKEN и AIRTABLE_BASE_ID
python bootstrap_airtable.py
```

Скрипт идемпотентен: создаст таблицы `Projects` (все поля §3 ТЗ) и `Settings` (одна запись с дефолтами §3.1). Существующие таблицы/поля пропускаются.

### Views (создаются вручную, ~2 минуты)

Web API Airtable не умеет создавать представления, поэтому в таблице `Projects` создайте:

| View | Настройка |
|---|---|
| **«Сегодня»** (по умолчанию) | Filter: `Created At` is `today` AND `Status` is not `Удалён`. Group by `Category`. Sort: `Posted At` ↓ |
| **«За 14 дней»** | Filter: `Created At` is within `the past 14 days` AND `Status` is not `Удалён` |
| **«Интересует»** | Filter: `Status` is `Интересует` |

«Интересует» / «Удалить» проект = сменить `Status` в записи; `Удалён` скрывается из рабочих views, физически строку удалит retention-очистка.

### Доступ команды
- Airtable: **Share → Invite** по email с правом Edit (для смены статусов и настроек). На бесплатном тарифе при нехватке мест — общая read-only ссылка (view share).
- Telegram: все сотрудники в общей группе получают пуши.

## Шаг 3. Настройки без кода (таблица Settings)

Одна строка, скрипт читает её в начале каждого запуска — правки подхватываются на следующем прогоне без деплоя:

| Поле | Что делает | Дефолт |
|---|---|---|
| `Enabled` | глобальный вкл/выкл трекера | ✅ |
| `Search Queries` | поисковые запросы Apify, по одному в строке; перебираются по одному за прогон (round-robin) | `Unreal Engine` |
| `Keyword Filter` | ключевые слова грубого фильтра, через запятую | список §4.1 ТЗ |
| `Active Start` / `Active End` | рабочее окно `HH:MM`; окно может переходить через полночь (`11:00`→`02:00`) | `11:00` / `02:00` |
| `Timezone` | IANA-таймзона окна | `Asia/Bangkok` |
| `Retention Days` | сколько дней хранить записи | `14` |

Вне окна или при `Enabled=false` скрипт выходит, **не** обращаясь к Apify и AI.

## Шаг 4. Локальный прогон и тесты

```bash
# офлайн-тесты (окно через полночь, фильтр, парсинг JSON, round-robin, нормализация)
pytest tests/test_offline.py -v

# живой тест классификатора: 2 эталонных + 3 отрицательных примера из ТЗ
ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_classifier_live.py -v

# полный цикл локально (нужны все ключи в .env)
python -m src.main
```

## Шаг 5. GitHub Actions

1. Создать репозиторий (лучше **публичный** — у публичных Actions-минуты безлимитны; секреты при этом не раскрываются), залить код.
2. **Settings → Secrets and variables → Actions → New repository secret** — добавить:
   `APIFY_TOKEN`, `APIFY_ACTOR_ID` (= `flash_mage/upwork`), `ANTHROPIC_API_KEY`, `AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. Ручная проверка: вкладка **Actions → scan-upwork → Run workflow** (`workflow_dispatch`). В логах шага `python -m src.main` видна сводка прогона; в Airtable появятся вакансии, в Telegram — пуши.
4. Дальше cron `*/30 4-19 * * *` (UTC) запускает сканирование сам. Точное рабочее окно проверяет код по `Settings` — если сдвинете окно далеко за пределы 04:00–19:00 UTC, расширьте и cron в [scan.yml](.github/workflows/scan.yml).

## Стоимость

- **Apify** (flash_mage): ~$0.001/вакансия × 5 вакансий × ~30 прогонов/день ≈ **$5–9/мес** (бесплатный кредит Apify $5/мес может покрыть старт).
- **Anthropic**: Haiku 4.5, ~400 токенов вывода на вакансию после грубого фильтра — единицы центов в день.
- **GitHub Actions**: бесплатно для публичного репозитория.
- Путь к нулю по источнику: подать заявку на официальный Upwork API (`marketplaceJobPostingsSearch`) и после одобрения убрать Apify.

## Как это защищено от дублей и повторных пушей

- Дедуп по `Upwork ID` до вызова AI — повторные прогоны не создают дублей и не тратят токены.
- Запись создаётся с `Notified=false`; шаг уведомлений берёт из Airtable все `Notified=false`, шлёт **один** пуш и только после успешной отправки ставит галочку. Если прогон упал между записью и отправкой — следующий прогон дошлёт.
- Сбой Apify / AI / сети по одной вакансии логируется и не прерывает прогон.

## Чек-лист приёмки (§11 ТЗ)

| Критерий | Статус |
|---|---|
| Ручной запуск `workflow_dispatch` добавляет вакансии в Airtable | код готов; проверить после заливки секретов |
| Cron каждые 30 мин; повторные прогоны без дублей и повторных пушей | ✅ дедуп по `Upwork ID` + флаг `Notified` |
| Грубый фильтр отсекает мусор до AI | ✅ `filters.py`, покрыт тестами |
| Haiku корректно ставит `relevant` и категорию на примерах §4.2 | ✅ `tests/test_classifier_live.py` (нужен API-ключ) |
| Один пуш в Telegram на новую вакансию с рабочей ссылкой | ✅ `telegram.py` + `Notified` |
| View «Сегодня» с группировкой по категориям; «За 14 дней» | создать вручную по инструкции выше (API не умеет) |
| Смена `Status` работает; `Удалён` скрыт в рабочих views | ✅ поле Status + фильтры views |
| Записи старше `Retention Days` удаляются автоматически | ✅ очистка в конце каждого прогона |
| Вне окна / `Enabled=false` — выход без обращений к Apify/AI | ✅ проверка до любых запросов, покрыто тестами |
| Изменения Settings подхватываются на следующем прогоне | ✅ чтение Settings в начале каждого запуска |
| Пуши в общий групповой чат | ✅ `TELEGRAM_CHAT_ID` = id группы |
| Секреты только в GitHub Secrets / env | ✅ `.env` в `.gitignore`, в коде ключей нет |
