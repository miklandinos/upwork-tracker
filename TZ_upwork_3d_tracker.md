# ТЗ: Трекер UE5-проектов с Upwork (Airtable-связка)

> Документ-задание для **Claude Code (модель Fable 5)**. Собери систему, которая каждые 30 минут забирает свежие вакансии с Upwork, классифицирует их через Claude Haiku 4.5, складывает в Airtable и шлёт пуш в Telegram.

---

## 0. Итоговая связка (утверждено)

| Роль | Сервис |
|---|---|
| Источник вакансий | **Apify** (актор Upwork Jobs Scraper) |
| Классификатор (runtime) | **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) |
| Хранилище + интерфейс трекера | **Airtable** |
| Планировщик (каждые 30 мин) | **GitHub Actions** (cron) |
| Уведомления | **Telegram Bot** |
| Язык скрипта | **Python 3.11** (один репозиторий, один скрипт) |

Важно: **Fable 5** используется только чтобы построить эту систему в Claude Code. Внутри системы, на каждую вакансию, работает **Haiku 4.5** — это дёшево и быстро. Не путать роли.

Airtable = сам трекер (референс вида: `https://upwork-3d-leads.vercel.app/`). Свой веб-дашборд НЕ пишем — используем представления (views) Airtable.

---

## 1. Как работает система (один цикл, каждые 30 минут)

```
GitHub Actions (cron */30)
  → прочитать таблицу Settings (§3.1): Enabled, рабочее окно, критерии
  → если Enabled=false ИЛИ сейчас вне рабочего окна → выйти (ничего не делать)
  → запустить Apify-актор с поисковыми запросами из Settings
  → получить JSON свежих вакансий
  → грубый фильтр по ключевым словам (§4.1)
  → дедуп: отбросить те, чей upwork_id уже есть в Airtable
  → новые прогнать через Haiku 4.5 (§4.2) → relevant? + категория
  → relevant=true записать в Airtable (status=new)
  → для каждой новой записи → пуш в Telegram (§6), пометить notified=true
  → раз в сутки: удалить записи старше 14 дней
```

Никакого постоянно работающего сервера нет — только скрипт по расписанию.

---

## 2. Структура репозитория

```
/upwork-3d-tracker
  /src
    main.py            # точка входа: весь цикл §1
    apify_client.py    # запуск актора, нормализация вакансий
    classifier.py      # промпт + вызов Haiku 4.5
    airtable_client.py # дедуп, запись, чтение, очистка
    telegram.py        # отправка пушей
    config.py          # чтение env
    filters.py         # грубый фильтр по ключам
  bootstrap_airtable.py # разовое авто-создание таблиц Projects и Settings по схеме §3/§3.1
  /.github/workflows
    scan.yml           # cron каждые 15 минут
  requirements.txt
  .env.example
  README.md
```

Секреты — только через переменные окружения / GitHub Secrets, никогда в коде.

---

## 3. Airtable: схема базы

Одна база, одна таблица `Projects`. Поля:

| Поле | Тип Airtable | Назначение |
|---|---|---|
| `Upwork ID` | Single line text | id вакансии, ключ дедупликации |
| `Title` | Single line text | заголовок (можно как ссылку через `URL`) |
| `URL` | URL | ссылка на вакансию |
| `Category` | Single select | `Недвижимость`, `Геймдев`, `Другие сферы`, `Веб-интерактив` |
| `Summary RU` | Long text | краткая суть на русском от AI |
| `Budget` | Single line text | бюджет/ставка |
| `Job Type` | Single select | `Fixed`, `Hourly` |
| `Country` | Single line text | страна клиента |
| `Client Spent` | Single line text | сколько клиент потратил на Upwork |
| `Posted At` | Date (with time) | дата публикации на Upwork |
| `Status` | Single select | `Новый`, `Интересует`, `Удалён` (default `Новый`) |
| `AI Confidence` | Number (0–1) | уверенность модели |
| `AI Reason` | Long text | обоснование от AI |
| `Notified` | Checkbox | отправлено ли в Telegram |
| `Created At` | Created time | когда попал в трекер (авто) |

### Представления (views) — это и есть UI трекера

- **«Сегодня»** (по умолчанию): фильтр `Created At = today` И `Status ≠ Удалён`. Группировка по `Category`. Сортировка по `Posted At` (новые сверху).
- **«За 14 дней»**: `Created At` за последние 14 дней, `Status ≠ Удалён`.
- **«Интересует»**: `Status = Интересует`.
- «Удалить» проект = поставить `Status = Удалён` (или удалить строку). «Интересует» = переключить `Status`.

> Кнопки «Интересует»/«Удалить» реализуются штатным полем `Status` (Single select) — отдельный код не нужен. По желанию в Airtable можно добавить Button-поля, меняющие статус через автоматизацию.

### 3.1. Таблица `Settings` — настройки без кода

Вторая таблица в той же базе, **одна запись** (одна строка). Скрипт читает её в начале каждого запуска. Это даёт сотрудникам менять критерии и часы прямо в Airtable, не трогая код.

| Поле | Тип | Назначение | Значение по умолчанию |
|---|---|---|---|
| `Enabled` | Checkbox | глобальный вкл/выкл трекера | ✅ |
| `Search Queries` | Long text | поисковые запросы для Apify, по одному в строке | см. §5 |
| `Keyword Filter` | Long text | ключевые слова грубого фильтра, через запятую | см. §4.1 |
| `Active Start` | Single line text | начало рабочего окна, `HH:MM` | `11:00` |
| `Active End` | Single line text | конец рабочего окна, `HH:MM` (может быть за полночь) | `02:00` |
| `Timezone` | Single line text | IANA-таймзона | `Asia/Bangkok` |
| `Retention Days` | Number | сколько дней хранить проекты | `14` |

Логика рабочего окна: окно `11:00 → 02:00` переходит через полночь, поэтому проверка «активно ли сейчас»:
`active = (now >= Active Start) OR (now <= Active End)` (в таймзоне `Timezone`).
Если `Enabled=false` или время вне окна — скрипт молча завершается, ничего не запрашивая (экономит Apify и токены).

Значения из `Settings` имеют приоритет над дефолтами в коде; если таблица недоступна — использовать дефолты из `config.py`.

---

## 4. Критерии отбора

### 4.1. Грубый фильтр (в коде, до AI)

Пропускаем вакансию к AI, если в `Title` ИЛИ `Description` встречается хотя бы одно (регистр игнорировать):

```
unreal engine, ue5, ue4, unreal, pixel streaming, archviz, arch viz,
architectural visualization, virtual tour, 3d tour, real-time 3d,
realtime 3d, interactive 3d, twinmotion, metahuman
```

Всё, что не прошло — молча отбрасываем (не тратим токены).

### 4.2. Классификатор на Haiku 4.5

Модель получает `Title` + `Description`, возвращает **строго JSON** (без пояснений вокруг):

```json
{
  "relevant": true,
  "category": "real_estate | gamedev | other | web_interactive_realestate",
  "confidence": 0.0,
  "reason": "1–2 предложения: почему подходит и в какую категорию",
  "summary_ru": "краткая суть проекта на русском, 1–2 предложения"
}
```

Маппинг `category` → поле Airtable `Category`:
`real_estate → Недвижимость`, `gamedev → Геймдев`, `other → Другие сферы`, `web_interactive_realestate → Веб-интерактив`.

**Системный промпт классификатора (вставить в `classifier.py`):**

```
Ты — фильтр вакансий с Upwork для студии, которая делает интерактивные 3D-решения
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

Отвечай ТОЛЬКО валидным JSON по заданной схеме. Без markdown, без комментариев.
```

**Few-shot примеры (зашить в промпт классификатора):**

**Положительный пример 1 → `other` (UE для общего окружения/города):**
```
Title: Unreal Engine Artist Needed to Build Realistic 3D Jeddah City From Scratch
Description: We need an experienced Unreal Engine artist or team to build a realistic 3D
Jeddah city from scratch. The project involves creating a detailed, immersive environment
that accurately represents the city's architecture, streets, and surroundings. This is a
long-term opportunity for someone who can deliver high-quality, visually compelling work
and collaborate effectively throughout the development process.
(Hourly, 6+ months, Intermediate, Mandatory skill: Unreal Engine. Client: Saudi Arabia, $57K spent, 9 hires.)
```
Ожидаемый JSON:
```json
{"relevant": true, "category": "other", "confidence": 0.9,
 "reason": "Разработка реалистичного 3D-города на Unreal Engine — реал-тайм 3D-окружение, но не про продажу недвижимости и не игра.",
 "summary_ru": "Нужен UE-художник/команда, чтобы с нуля построить реалистичный 3D-город Джидда (архитектура, улицы, окружение). Долгосрочно."}
```

**Положительный пример 2 → `real_estate` (интерактивный конфигуратор квартиры для продаж):**
```
Title: Unreal Engine Developer for Interactive Content
Description: We are a technology company building a sales demo for off-plan real estate
developers: a photoreal, walkable luxury apartment in UE 5.5+ where a buyer walks up to the
kitchen, bathroom, or floor area, a branded widget appears, and choosing an upgrade option
(e.g., three kitchen packages, four floor finishes) swaps the model in real time with a
running price total. Data-driven customization system in Blueprints (proximity-triggered
widgets, UMG panels, instant material swaps, AED price ticker, save-to-JSON). PCVR support
(Quest 3 via Link, OpenXR). Packaged Windows build (60 fps @1440p). 8K 360 panoramas + cinematic.
Must have: photoreal UE5 interior archviz (Lumen), real Blueprint experience (DataTables, actor
components, event dispatchers, UMG), runtime material/mesh swapping (configurators, variant systems).
```
Ожидаемый JSON:
```json
{"relevant": true, "category": "real_estate", "confidence": 0.98,
 "reason": "Интерактивная фотореалистичная квартира на UE5.5 с конфигуратором отделки и ценником — продающий инструмент для застройщиков недвижимости.",
 "summary_ru": "Sales-demo для застройщиков: ходибельная фотореалистичная квартира на UE5.5 с конфигуратором отделки в реальном времени и подсчётом цены. Blueprints, PCVR, рендер панорам."}
```

**Отрицательные примеры (relevant=false), тоже зашить в промпт:**
```
Title: Unity Mobile Game Developer for Casual Puzzle Game
→ {"relevant": false, "category": "gamedev", "confidence": 0.95, "reason": "Движок Unity, не Unreal — вне профиля.", "summary_ru": "Разработка мобильной казуальной игры на Unity."}

Title: Edit My YouTube Videos and Add Motion Graphics
→ {"relevant": false, "category": "other", "confidence": 0.97, "reason": "Монтаж видео и 2D-графика, нет реал-тайм 3D / Unreal.", "summary_ru": "Монтаж роликов для YouTube и моушн-графика."}

Title: 3D Product Modeling in Blender for E-commerce
→ {"relevant": false, "category": "other", "confidence": 0.9, "reason": "Только моделинг в Blender, без интерактива и без Unreal.", "summary_ru": "Моделинг товаров в Blender для интернет-магазина."}
```

Параметры вызова: `model="claude-haiku-4-5-20251001"`, `max_tokens≈400`, `temperature=0`.

---

## 5. Apify: получение вакансий

**Основной актор: `flash_mage/upwork`** (actorId `QRHJxnLxIAxpfHCcu`) — оплата строго за результат (~$0.001/вакансия), без минимума 10 за запуск. Именно это делает частый опрос дешёвым.

- Запуск: `POST https://api.apify.com/v2/acts/flash_mage~upwork/run-sync-get-dataset-items?token=$APIFY_TOKEN`, тело — вход актора.
- **Один запрос за цикл, только 5 самых свежих вакансий** (минимум актора — 5 строк). Дубли отсекаются дедупом по Airtable, свежесть — сортировкой `newest`. Этого с запасом хватает для ниши, где всего ~20–40 UE-вакансий в день.
- Основной запрос: `Unreal Engine`. Дополнительные запросы из `Settings.Search Queries` перебирать по одному за цикл (round-robin), не все сразу.

Вход актора (JSON body):
```json
{
  "keyword": "Unreal Engine",
  "sort": "newest",
  "rows": 5
}
```

> Точные имена полей входа сверить на `apify.com/flash_mage/upwork/input-schema` при реализации (актор ищет «each keyword separately», сортировка `newest`/`relevance`/`default`, лимит строк 5–500). Если имя параметра лимита иное — использовать минимально допустимое (5).

Выходная схема даёт `title`, `link` (URL), `description`, `id`, `jobType`, `publishTime`/`createTime`, бюджет (`fixedPriceAmount` / `hourlyBudgetMin|Max`), навыки, данные клиента (`buyer.stats.totalCharges`, `buyer.location.country`). Нормализовать в единый объект: `upwork_id, title, url, description, budget, job_type, country, client_spent, posted_at`. Значения, которых нет, — `null`.

### 5.1. Стоимость и биллинг

- **flash_mage**: ~$0.001/вакансия, без минимума 10 за запуск (минимум 5 строк). При 5 свежих × ~30 прогонов/день (30-мин интервал в окне ~15ч) ≈ 150 вакансий/день ≈ **~$5–9/мес** (плюс копеечные прокси-сборы Apify).
- Бесплатный триал Apify: $5 кредита/мес — на старте может хватать целиком.
- **Запасной актор (надёжный): `neatrat/upwork-job-scraper`** (actorId `XYTgO05GT5qAoSlxy`, вход `query`+`maxJobAge`+`sort`). Дороже (мин. 10 вакансий/запуск, ~$3.20/1000 → ~$29/мес на 30 мин), но обкатан (1.3M запусков). Переключение — через переменную `APIFY_ACTOR_ID`, код источника должен поддерживать оба формата входа/выхода (адаптер под актор).
- **Путь к нулю:** параллельно подать заявку на официальный Upwork API (`marketplaceJobPostingsSearch`) — бесплатно; после одобрения Apify можно убрать.

---

## 6. Telegram-пуш

На каждую новую запись (Notified=false) — одно сообщение (HTML):

```
🟢 <b>{Категория}</b>
<b>{Title}</b>
💰 {Budget} · 🌍 {Country}
🧠 {Summary RU}

🔗 {URL}
```

Кнопку-ссылку «Открыть в Airtable» можно добавить (URL записи). Действия «Интересует»/«Удалить» делаются в Airtable (интерактивные кнопки в Telegram потребовали бы постоянного сервера-вебхука — в MVP не делаем). После отправки — проставить `Notified=true`.

Секреты: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

> **Несколько сотрудников:** чтобы пуши видел не только владелец, `TELEGRAM_CHAT_ID` указывать **id группового чата**, а не личный. Создать группу, добавить туда бота, получить chat_id группы (например, временно включить логирование `getUpdates` или добавить в группу @RawDataBot). Все сотрудники в группе получают уведомления.

### 6.1. Доступ команды к трекеру

- **Airtable:** пригласить сотрудников как коллабораторов базы (Share → пригласить по email). На бесплатном тарифе есть лимит коллабораторов — при нехватке использовать общий доступ по ссылке (view share) в режиме чтения либо перейти на платный план. Редактировать статусы/настройки могут только приглашённые с правом Edit.
- **Telegram:** общий групповой чат (см. выше).

---

## 7. Планировщик — GitHub Actions

`.github/workflows/scan.yml`:

```yaml
name: scan-upwork
on:
  schedule:
    # Каждые 30 минут в UTC-окне, покрывающем 11:00–02:00 по Таиланду (ICT = UTC+7).
    # 11:00 ICT = 04:00 UTC; 02:00 ICT = 19:00 UTC. Точное окно всё равно проверяется в коде по Settings.
    - cron: "*/30 4-19 * * *"
  workflow_dispatch: {}        # ручной запуск для теста
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: python -m src.main
        env:
          APIFY_TOKEN:        ${{ secrets.APIFY_TOKEN }}
          APIFY_ACTOR_ID:     ${{ secrets.APIFY_ACTOR_ID }}
          ANTHROPIC_API_KEY:  ${{ secrets.ANTHROPIC_API_KEY }}
          AIRTABLE_TOKEN:     ${{ secrets.AIRTABLE_TOKEN }}
          AIRTABLE_BASE_ID:   ${{ secrets.AIRTABLE_BASE_ID }}
          AIRTABLE_TABLE:     Projects
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID:   ${{ secrets.TELEGRAM_CHAT_ID }}
```

Примечания:
- Cron заведомо шире рабочего окна; **точное окно определяет код по таблице `Settings`** (§3.1). Изменил часы в Airtable — поведение поменялось без правки cron/кода. Если окно сдвинут далеко за пределы 04:00–19:00 UTC, тогда расширить и cron.
- GitHub Actions cron может запускаться с задержкой в пиковые часы — для 15 мин это приемлемо.
- Раз в сутки очистка старше `Retention Days`: отдельным шагом с проверкой времени, либо вторым workflow `cron: "0 3 * * *"`.
- Репозиторий лучше публичный (у публичных Actions-минуты безлимитны). Секреты это не раскрывает — они в GitHub Secrets.

---

## 8. Переменные окружения (`.env.example`)

```
APIFY_TOKEN=
APIFY_ACTOR_ID=flash_mage/upwork   # запасной надёжный: neatrat/upwork-job-scraper
ANTHROPIC_API_KEY=
AIRTABLE_TOKEN=
AIRTABLE_BASE_ID=
AIRTABLE_TABLE=Projects
AIRTABLE_SETTINGS_TABLE=Settings
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

---

## 9. Пошаговое получение ключей (для заказчика)

**1. Telegram-бот**
- В Telegram открыть **@BotFather** → `/newbot` → задать имя и username → скопировать **токен** → в `TELEGRAM_BOT_TOKEN`.
- Для команды: создать **групповой чат**, добавить туда бота и всех сотрудников, добавить @RawDataBot (или @getidsbot) → он покажет **chat_id группы** (число с минусом) → в `TELEGRAM_CHAT_ID`. Для личного использования — chat_id можно взять у @userinfobot.

**2. Apify**
- Регистрация на **apify.com** → Console → **Settings → Integrations → API tokens** → скопировать токен → в `APIFY_TOKEN`.
- Использовать актор **`flash_mage/upwork`** (дешёвый, оплата за результат). На его странице открыть вкладку Input — сверить точные имена параметров (§5).

**3. Anthropic API (Haiku)**
- **console.anthropic.com** → привязать оплату (Billing) → **API Keys → Create Key** → скопировать → в `ANTHROPIC_API_KEY`.

**4. Airtable**
- **airtable.com** → Create → Start from scratch → создать **пустую** базу (таблицы создаст `bootstrap_airtable.py`, §10 п.0).
- **Base ID**: из URL базы часть `appXXXXXXXXXXXXXX`, либо airtable.com/developers/web/api → в `AIRTABLE_BASE_ID`.
- **Token**: airtable.com/create/tokens → Create token → scopes `data.records:read`, `data.records:write`, `schema.bases:read`, `schema.bases:write` → в Access добавить свою базу → создать → в `AIRTABLE_TOKEN`.
- Запустить один раз `python bootstrap_airtable.py` — создаст таблицы `Projects` и `Settings`.
- Пригласить сотрудников: **Share → Invite** (с правом Edit — для смены статусов/настроек).
- **Base ID**: открыть базу → в URL часть `appXXXXXXXX`, либо airtable.com/developers → Base ID → в `AIRTABLE_BASE_ID`.
- **Token**: airtable.com → **Developer hub → Personal access tokens → Create token** → scopes: `data.records:read`, `data.records:write`, `schema.bases:read`; дать доступ к нужной базе → скопировать → в `AIRTABLE_TOKEN`.

**5. GitHub**
- Создать репозиторий, залить код → **Settings → Secrets and variables → Actions → New repository secret** — добавить все переменные из §8.

---

## 10. Этапы реализации (для Claude Code)

0. `bootstrap_airtable.py` — разовый скрипт: через Airtable Web API (`schema.bases:write`) создать в существующей базе таблицы `Projects` (§3) и `Settings` (§3.1) со всеми полями и заполнить `Settings` дефолтами. Идемпотентно: если таблица/поле уже есть — пропускать. Токен нужен со scope `schema.bases:write`.
1. Каркас репозитория, `requirements.txt` (`requests`, `anthropic`, `pyairtable`), `config.py`.
2. `apify_client.py` — запуск актора + нормализация.
3. `filters.py` — грубый фильтр по ключам.
4. `classifier.py` — промпт + вызов Haiku, парсинг JSON, обработка ошибок. **Юнит-тест**: прогнать эталонные вакансии из §4.2 и 2–3 отрицательных примера, проверить категории.
5. `airtable_client.py` — дедуп по `Upwork ID`, запись, чтение, очистка 14 дней.
6. `telegram.py` — отправка пушей.
7. `main.py` — склейка цикла §1 + идемпотентность (повторный запуск не плодит дубли и повторные пуши).
8. `scan.yml` — GitHub Actions.
9. `README.md` — как поднять: ключи, создать базу, залить секреты, запустить `workflow_dispatch` вручную для проверки.

---

## 11. Критерии приёмки

- [ ] Ручной запуск (`workflow_dispatch`) отрабатывает без ошибок и добавляет вакансии в Airtable.
- [ ] Cron реально идёт каждые 30 минут; повторные запуски **не создают дублей** (дедуп по `Upwork ID`) и **не шлют повторные пуши**.
- [ ] Грубый фильтр отсекает явный мусор ещё до AI (экономит токены).
- [ ] Haiku корректно ставит `relevant` и категорию: оба эталонных примера из §4.2 попадают в `Недвижимость`/`Веб-интерактив`; отрицательные примеры — отсекаются.
- [ ] На каждую новую подходящую вакансию приходит **один** пуш в Telegram с рабочей ссылкой.
- [ ] Представление «Сегодня» в Airtable по умолчанию показывает только сегодняшние проекты, сгруппированные по категориям; доступен просмотр за 14 дней.
- [ ] Смена `Status` на `Интересует`/`Удалён` работает в Airtable; `Удалён` не показывается в рабочих views.
- [ ] Записи старше `Retention Days` удаляются автоматически.
- [ ] Вне рабочего окна (по умолчанию 11:00–02:00 Asia/Bangkok) и при `Enabled=false` скрипт завершается без обращений к Apify/AI.
- [ ] Изменение `Search Queries`, `Keyword Filter` и рабочих часов в таблице `Settings` подхватывается на следующем запуске без правки кода.
- [ ] Пуши приходят в общий групповой чат Telegram; сотрудники видят проекты и могут менять статусы в Airtable.
- [ ] Все секреты — в GitHub Secrets, в коде их нет.

---

## 12. Будущие улучшения (не в MVP)

- Интерактивные кнопки прямо в Telegram (потребует лёгкий вебхук-сервер, напр. Vercel-функция).
- Миграция с Apify на официальный Upwork API (`marketplaceJobPostingsSearch`) — бесплатно и легально, когда одобрят ключ.
- Автогенерация черновика отклика (cover letter) на проекты со статусом «Интересует».
- Скоринг клиента (сколько потрачено, % найма, страна) и сортировка по «качеству лида».
- Ночью сканировать реже (экономия Apify), днём — каждые 15 минут.
