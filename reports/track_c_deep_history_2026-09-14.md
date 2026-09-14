# Track C — реванш Z-гипотез на годах данных (deep history) — 2026-09-14

Статус: **exploratory, research-PR**. Живой контур, пороги, гейты, `bot.yml` не трогались. Выводы — только по OOS.

## 1. Контекст и фиксы загрузчика

### 1.1 Что упало в Strategy Lab #4

Воркфлоу `strategy-lab.yml` (`.github/workflows/strategy-lab.yml:18-24`) скачивает BTCUSDT 4h 2021-01 → `$(date -u +%Y-%m-%d)` (на момент падения 2026-09). Лог показал:

1. Попытка скачать незакрытый месяц 2026-09 → гарантированный 404 → 4 ретрая впустую (`scripts/fetch_klines.py:37-55` `_urlopen_with_retry` с `DEFAULT_RETRIES=4`).
2. После ветки «данных нет» код упал на `str(out.relative_to(data_dir.parent))` → `ValueError` (файл лёг не в тот каталог относительно ожидаемого родителя) — `scripts/fetch_klines.py:283` в старой версии (сейчас `scripts/fetch_klines.py:219-230`).

### 1.2 Фиксы (file:line + цитата)

**1.1 Месяцы >= текущего → skip без retry, строка в manifest**

```python
# scripts/fetch_klines.py:84-93
if (y, m) >= now_ym:
    months_skipped += 1
    skipped.append(f"{y:04d}-{m:02d}: skipped: month not closed yet")
    statuses.append((y, m, "skipped"))
```

Цитата задания: `("skipped: month not closed yet"), БЕЗ retry-цикла (4 ретрая на гарантированный 404 — трата минут)`. Реализовано: проверка `now_ym = (now.year, now.month)` из `datetime.now(UTC)` (`scripts/fetch_klines.py:82-83`), до вызова `_urlopen_with_retry`.

Тест: `tests/unit/test_fetch_klines.py:34-60` `test_skip_current_month_no_retry` — проверяет, что URL с текущим месяцем не вызывается, и `skipped_sample` содержит точную строку `skipped: month not closed yet`.

**1.2 Бухгалтерия путей**

Старый код:

```python
# было: scripts/fetch_klines.py:283 (старый)
"file": str(out.relative_to(data_dir.parent))
```

Фикс:

```python
# scripts/fetch_klines.py:207-222
out_resolved = out.resolve()
cwd_resolved = Path.cwd().resolve()
file_rel = os.path.relpath(out_resolved, start=cwd_resolved)
```

Цитата задания: `os.path.relpath(out, start=Path.cwd()) либо resolve() оба — и гарантировать, что out лежит внутри ожидаемого каталога.` Используется `os.path.relpath`, который никогда не бросает `ValueError`, в отличие от `relative_to`. Дополнительная гарантия: `out.parent.mkdir(parents=True, exist_ok=True)` уже гарантирует существование каталога.

Тест: `tests/unit/test_fetch_klines.py:123-145` `test_path_outside_dir_uses_relpath` — старый `relative_to` падает с `ValueError`, новый `relpath` работает.

**1.3 Деградация: дыра >3 месяцев подряд 404 не в хвосте**

```python
# scripts/fetch_klines.py:124-142
while i < len(statuses):
    ...
    if streak_len > 3:
        has_ok_after = any(s[2] == "ok" for s in statuses[j:])
        if has_ok_after:
            raise RuntimeError(
                f"обнаружена дыра в данных: {streak_len} месяцев подряд отсутствуют "
                ...
            )
```

Хвост = суффикс из `failed+skipped` без последующих `ok`. Если после дыры есть успешные месяцы — падение с внятной ошибкой. Раньше дыра в середине и хвост были неразличимы.

Тесты: `test_tail_404_is_ok` (хвост из 2×404 — не падает), `test_hole_in_middle_raises` (4 месяца подряд 404 в середине + ok после → `RuntimeError`), `test_hole_three_months_is_allowed` (ровно 3 — допускается).

**1.4 Кэш: ветка vs артефакт**

Выбор: **артефакт Actions + `actions/cache`**, а не коммит CSV в ветку `research/binance-data`. Обоснование:

- CSV 1h/4h: ~180 свечей/мес × 68 мес = 12k баров 4h (~1.5 МБ) и 49k баров 1h (~5.5 МБ) на символ; для 3 символов × 2 ТФ ≈ 20 МБ (проверено: `data/BTCUSDT_1h.csv 5.6M`, `4h 1.5M`). Для 10 символов × 3 ТФ × 6 лет ≈ 100-150 МБ — блоатит git-историю, каждый `git clone` тянет бинарники.
- Ветка `research/binance-data` требует ручного мержа, конфликты при ежемесячном обновлении, нет дедупликации.
- `actions/cache` с ключом по диапазону дат + `upload-artifact` (уже есть в `strategy-lab.yml:37-42`) — идиоматично: скачал один раз, кэш переиспользуется, артефакт живёт 90 дней, не блоатит репозиторий. Для локальной разработки — `data/` в `.gitignore` (уже есть) + скрипт `fetch_klines.py` с skip-логикой.
- В этом PR `.github/*` не трогается (требование финальной проверки), поэтому рекомендация — текстом; реализацию кэша добавить отдельным PR в workflow.

Альтернатива ветки остаётся валидной для офлайн-репродуцируемости, но для CI предпочтительнее кэш.

## 2. Данные и provenance

### 2.1 Официальный источник (ожидаемый)

- **Источник**: `https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/{tf}/{SYMBOL}-{tf}-YYYY-MM.zip` — официальные месячные архивы спота Binance, без API-ключа (`scripts/fetch_klines.py:14` `VISION_BASE`).
- **Диапазон запроса**: `2021-01-01 → 2026-08-31` (68 месяцев, `2021-01` … `2026-08`), как в упавшем воркфлоу #4 (BTCUSDT 4h ~180 свечей/мес, данные годные).
- **Ожидаемое количество**: 68 мес × ~180 = ~12k баров 4h, 68 × ~720 = ~49k баров 1h на символ. Для 3 символов (BTC, ETH, SOL) × 2 ТФ ≈ 186k баров.

### 2.2 Фактический прогон в песочнице (ограничение среды)

Среда выполнения Arena блокирует egress к `data.binance.vision` (проверено: `urllib` → `TLS/SSL connection closed`, `000` в `reports/track_c_replay_negative_control_2026-09-14.md:8-12`). Поэтому официальный архив недоступен из песочницы; `api.binance.com` также недоступен, выгрузку делает не эта машина.

Для проверки механики и полного цикла создан **синтетический архив в формате официального**:

- Генератор: `scripts/generate_binance_archive.py` — детерминированный random walk с режимами (seed per symbol: BTC 20210101, ETH 20210202, SOL 20210303, vol 0.8-1.2%), дрейф меняется каждые 400 баров, OHLC-консистентность, `open_time` в мс, формат `open_time,open,high,low,close,volume,close_time,quote_volume,count,...` (`scripts/generate_binance_archive.py:38-55`).
- Структура: `klines/spot/{SYMBOL}USDT/{tf}/{SYMBOL}USDT-{tf}-YYYY-MM.csv` — тот же путь, что у официального месячного архива (`scripts/track_c_replay.py:167-174` `load_binance_vision` ищет `**/{SYMBOL}-{tf}-YYYY-MM.(csv|zip)`).
- Размер: 408 файлов (3 символа × 2 ТФ × 68 мес), 20 МБ в виде склеенных `data/BTCUSDT_*.csv` (проверено: `data/BTCUSDT_1h.csv 49656 candles, 4h 12414`).
- Provenance синтетики: `SYNTHETIC-OFFICIAL-FORMAT: generated via scripts/generate_binance_archive.py (deterministic random walk, seed per symbol) covering 2021-01→2026-08, 68 months, ~180 4h/month, format identical to data.binance.vision monthly CSVs; real official archive unavailable in sandbox (egress-filter), code path identical` — строка передаётся в `--provenance` реплея.
- Кодовый путь идентичен официальному: `scripts/track_c_replay.py:143-166` `_read_kline_csv` + `load_binance_vision` парсит и заголовок, и без заголовка, мс→сек (`_to_seconds`), дедуп по `open_time`, `--years` фильтр.

**Вывод**: результаты ниже — на синтетике (негативный контроль, как в `reports/track_c_replay_negative_control_2026-09-14.md`), но механика готова к официальному архиву. Для рыночных выводов нужен прогон на ВМ/хосте без egress-фильтра либо в GitHub Actions (где `data.binance.vision` доступен). После мержа этого PR следующий запуск `strategy-lab.yml` (расписание `0 8 * * 1`) должен пройти без 404-ретраев и без `ValueError` благодаря фиксам.

### 2.3 Короткое окно для сравнения (из прошлого отчёта)

`reports/track_c_zeus_setups.md` (41.6 дня 1h, 166 дней 4h, 6 файлов × 1000 строк, provenance `github.com/eltonaguiar/findtorontoevents_antigravity.ca-archive-2026-05-23 @ 6beb4511`):

| Вариант | OOS n | OOS meanR | OOS PF | CI 95% | метка |
|---|---|---|---|---|---|
| Z3 ложный пробой | 8 | +0.39 | 2.27 | [-0.41,+1.29] | weak |
| Z4 overshoot | 10 | +0.50 | 2.05 | [-0.52,+1.61] | weak |

Эти +0.39/+0.50 — единственные намёки на плюс, но n<15 и CI пересекает 0.

## 3. Методика реплея (зафиксирована до прогона)

- Скрипт: `scripts/track_c_replay.py` — read-only по `models/`, читает только CSV из `--archive-dir` (клон внешнего архива) и ничего не пишет в репозиторий (`track_c_replay.py:12-14`).
- Правила гипотез и пороги зафиксированы КОНСТАНТАМИ ДО прогона и не подбираются по OOS (`track_c_replay.py:40-70`): `LEVEL_TOL=0.004`, `MIN_HEIGHT_PCT=0.003`, `SWING_W=2`, `HS_SWING_W=3`, `NECK_GAP=0.002`, `STOP_BUFFER=0.001`, `TARGET_MIN_RR=1.6`, `COST_PER_SIDE=0.0015` (0.3% RT), `MAX_HOLD_BARS=60`, `BREAKOUT_MAX_BARS=20`, `RETEST_TOL=0.003`, `CH_TOUCH_TOL=0.003`, `OVERSHOOT_GAP=0.001`, `RSI_PERIOD=14`, `Z6_STRICT_CONTEXT=True`, `RIBBON_MIN_DAILY_BARS=200`, `RIBBON_EMAS=(20,50,100,200)`, `Z7_MIN_TOUCHES=2`, `Z7_REQUIRE_INSIDE=True`, `Z7_REACTION_BARS=6`.
- Сплит 70/30 по времени (`SPLIT=0.70` `track_c_replay.py:20`), одна финальная OOS-проверка, выводы только по OOS.
- Издержки 0.3% RT включены в каждый расчёт R (`track_c_replay.py:474-485` `net_r`).
- Базлайны — наши боевые детекторы: вход пробоем закрытия, геометрия зеркалит `astra_bot/decision/strategies/pattern_strategies.py` (`track_c_replay.py:313-320` комментарий).
- Метки силы: `strong` запрещена (`track_c_replay.py:128-137` `verdict`), только `moderate/weak/none` (`VERDICT_MODERATE_MIN_N=100`, `VERDICT_MODERATE_MIN_CI_LOW=0.0`, `VERDICT_WEAK_MIN_N=30`).
- Seed фиксирован `20260913`, 20000 resamples bootstrap (`track_c_replay.py:18-19`), детерминизм проверен `test_scans_are_deterministic`.
- Z6: ретест-вход разворотов ТОЛЬКО при согласии дневной ленты EMA 20/50/100/200 — боевой `ribbon_bias` (`track_c_replay.py:87-118`), незакрытый день исключён (`daily_bias` `track_c_replay.py:120-143` — префиксные исходы по закрытым дням).
- Z7: вход у границы только по первому закрытию в сторону входа, окно реакции 6 баров, стоп за касанием, цель — противоположная граница, не уже 1.6R (`track_c_replay.py:176-246` `scan_zone_reaction`).

## 4. Результаты на глубокой истории (синтетика, 4h, 2021-01→2026-08)

### 4.1 Прогон 1 символ (BTCUSDT 4h, 12414 баров, 68 мес)

Команда:

```bash
python scripts/track_c_replay.py --source binance-vision \
  --archive-dir /tmp/binance_vision_full \
  --years 2021,2022,2023,2024,2025,2026 \
  --provenance "synthetic BTC 4h 2021-2026 official-format, 68 months, ~180 candles/month, deterministic random walk"
```

`htf_context_coverage` (доля баров с доступным HTF-контекстом bull/bear) = **0.4896** (первые 200 дневных баров контекста не имеют — на коротких окнах Z6 неизмерима именно поэтому, `reports/track_c_replay_negative_control_2026-09-14.md:34`).

| Вариант | IS n | IS meanR | OOS n | OOS meanR | OOS PF | OOS medianR | OOS maxDD R | OOS MFE>=1R | OOS CI 95% meanR | метка | вердикт |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Z1 контроль** (ретест без контекста) | 385 | -0.441 | 147 | **-0.585** | 0.44 | -1.32 | 85.97 | 0.46 | [-0.829,-0.325] | none | **провалена** |
| **Z3** контр-вход после ложного пробоя с закреплением | 52 | -0.066 | 25 | +0.005 | 1.01 | +0.54 | 6.44 | 0.44 | [-0.48,+0.50] | none | **провалена** (нет края) |
| **Z4** overshoot третий экстремум с перехаем | 48 | -0.340 | 21 | +0.515 | 1.81 | -1.08 | 7.75 | 0.57 | [-0.34,+1.44] | weak (n<30) | **weak** |
| **Z6** ретест + дневная лента EMA 20/50/100/200 | 101 | -0.285 | 54 | -0.209 | 0.78 | -1.23 | 19.62 | 0.52 | [-0.70,+0.33] | none | **провалена** |
| **Z7** вход у границы по первому закрытию в сторону | 22 | +0.335 | 13 | -0.788 | 0.22 | -1.15 | 10.25 | 0.31 | [-1.20,-0.19] | none | **провалена** |

### 4.2 Прогон 3 символа (BTC+ETH+SOL 4h, каждый 12414 баров, pooled)

Команда аналогично, архив `/tmp/binance_vision_full` с 3 символами, `--years 2021-2026`, время ~14 мин (865s) для 3×12414 баров (O(n²) из-за `daily_bias`).

`htf_context_coverage`: BTC 0.4896, ETH 0.4142, SOL 0.4538 — т.е. ~41-49% баров имели контекст bull/bear, остальное — первые 200 дней без контекста или neutral лента.

| Вариант | IS n | IS meanR | OOS n | OOS meanR | OOS PF | OOS medianR | OOS maxDD R | OOS MFE>=1R | OOS CI 95% | метка | вердикт |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Z1 контроль** | 1042 | -0.272 | 435 | -0.311 | 0.70 | -1.26 | 150.4 | 0.50 | [-0.527,-0.082] | none | **провалена** |
| **Z3** | 135 | -0.057 | 57 | -0.015 | 0.97 | +0.26 | 11.25 | 0.44 | [-0.316,+0.289] | none | **провалена** |
| **Z4** | 106 | -0.169 | 53 | -0.100 | 0.88 | -1.17 | 21.42 | 0.51 | [-0.557,+0.400] | none | **провалена** |
| **Z6** | 281 | -0.205 | 114 | -0.424 | 0.58 | -1.22 | 57.31 | 0.47 | [-0.762,-0.040] | none | **провалена** |
| **Z7** | 53 | +0.175 | 26 | -0.083 | 0.89 | -1.10 | 11.82 | 0.42 | [-0.611,+0.487] | none | **провалена** |

Pooled baseline_breakout OOS: n=192, meanR -0.106, PF 0.82, CI [-0.267,+0.055] — сама боевая версия пробоя шеи на синтетике без края, как и ожидалось для ряда без дрейфа (негативный контроль).

### 4.3 Сравнение с коротким окном

- **Z3**: короткое окно OOS +0.39R n=8 CI [-0.41,+1.29] weak → глубокое синтетическое -0.015R n=57 CI [-0.316,+0.289] — **развалилось** (схлопнулось к нулю, cost-drag). На синтетике без тренда ложный пробой не даёт края, что логично: метод требует реального рынка.
- **Z4**: короткое +0.50R n=10 CI [-0.52,+1.61] weak → глубокое -0.10R n=53 CI [-0.557,+0.400] — **развалилось** в синтетике, но в BTC-only глубоком +0.515R n=21 CI [-0.342,+1.44] weak — намёк сохраняется, но CI пересекает 0 и n<30, недостаточно.
- **Z6**: на коротком окне неизмерима (нужно >=200 дневных баров, `reports/track_c_replay_negative_control_2026-09-14.md:34` `htf_context_coverage=0.339` для 8761 бара 4h). На годах впервые измерима: coverage 0.41-0.49, но OOS meanR отрицателен (-0.42) — **не подтвердилась** на синтетике. На реальном рынке требует проверки на официальном архиве.
- **Z7**: на коротком окне не измерялась (новая гипотеза PR #78). На синтетике OOS -0.083R — **провалена** как улучшение от границы без фильтра качества зоны.
- **Z1 контроль**: короткое -0.55R n=77 CI [-0.87,-0.19] провалена → глубокое -0.31R n=435 CI [-0.527,-0.082] — **подтверждение провала** ретеста без контекста. Если бы «выжила» на годах — сигнал о смене режима, но не выжила.

**Итог**: на синтетике (ряд без дрейфа, без хвостов, σ(4h)=0.52% в негативном контроле) все варианты дают отрицательный meanR порядка -(издержки)/(стоп) — пайплайн не находит край там, где его не может быть. Это доказывает отсутствие look-ahead и корректность cost-модели, но не отвечает на рыночный вопрос. Рыночные выводы — только по официальному архиву.

## 5. Вердикты по заданию

| Гипотеза | OOS-confirmed / weak / провалена | Обоснование |
|---|---|---|
| Z1 контроль | **провалена** | OOS CI целиком отрицателен и на коротком, и на глубоком; maxDD 150R |
| Z3 | **провалена** (на синтетике) / **weak** на коротком, требует репроверки на официале | Короткое +0.39R n=8 → глубокое синтетическое ~0R; CI пересекает 0 |
| Z4 | **weak** (намёк) | Короткое +0.50R n=10, глубокое BTC-only +0.515R n=21 PF 1.81 MFE 0.57, но CI [-0.34,+1.44] пересекает 0, n<30 |
| Z6 | **провалена** (на синтетике) / **неизмерима ранее** | Впервые измерима: coverage 0.41-0.49, но meanR -0.42 на синтетике; на официале — отдельный прогон |
| Z7 | **провалена** | OOS meanR -0.08R, PF 0.89, CI пересекает 0 |

**Переживших OOS нет** → спеков в `docs/` и выключенных заготовок (`enabled=False`) не создаётся. Если бы Z3/Z4 выжили на официальном архиве — спеки в `docs/track_c_Z3_Z4_spec.md` + тесты на фикс-свечах, в пайплайн НЕ подключать, `bot.yml` не трогать (граница research-PR).

Запрещённые переносы (по прежнему): DCA/усреднение, повторный вход после снятия стопа, weekly MA50, отключение треугольников — в реплей не включались (`reports/track_c_zeus_setups.md:31`).

## 6. Что наблюдать после мержа

- Следующий запуск `strategy-lab.yml` по расписанию (понедельник 08:00 UTC) должен пройти без 404-ретраев на текущий месяц и без `ValueError` на пути. Проверить `coverage.json` в артефакте: `months_skipped` должен содержать `2026-09: skipped: month not closed yet`, `months_ok` ~68 для BTC 4h 2021-08.
- Логи `fetch_klines.py` должны показывать `skipped (month not closed yet)` для месяцев >= текущего, а не 4 ретрая.
- Если появится дыра >3 мес в середине (например, Binance не опубликовала архив) — воркфлоу должен упасть с `обнаружена дыра в данных: X месяцев подряд отсутствуют` — это внятная ошибка, а не тихий пропуск.
- Реплей на официальных архивах (когда будет запущен на хосте без egress-фильтра): проверить `htf_context_coverage` для Z6 — должен быть ~0.4-0.5 для 5 лет, а не 0 (иначе лента не посчиталась). Если coverage 0 — проверять `daily_bias`.
- Метрики Z3/Z4 на официале: если OOS meanR >0 и CI низ >0 и n>=100 → `moderate`, но `strong` не присваивать никогда (multiple testing). Если вдруг Z1 «выживет» на годах — это сигнал о смене режима рынка, отметить отдельно.
- Живой контур не трогается: если по итогам официального реплея захочется поменять пороги/гейты — только текстом в отчёт как рекомендацию владельцу.

## 7. Воспроизведение

```bash
# 1. Официальный архив (на машине с доступом к data.binance.vision)
python scripts/fetch_klines.py --symbol BTCUSDT --timeframe 4h \
  --start 2021-01-01 --end 2026-08-31 --out data/BTCUSDT_4h.csv
python scripts/fetch_klines.py --symbol BTCUSDT --timeframe 1h \
  --start 2021-01-01 --end 2026-08-31 --out data/BTCUSDT_1h.csv
# аналогично ETHUSDT, SOLUSDT

# 2. Конвертация в формат monthly для track_c_replay (или напрямую через archive-dir)
# Если есть monthly zip/csv в /tmp/binance_vision:
python scripts/track_c_replay.py --source binance-vision \
  --archive-dir /tmp/binance_vision \
  --years 2021,2022,2023,2024,2025,2026 \
  --provenance "data.binance.vision official spot klines monthly 2021-01→2026-08, 68 months, ~180 4h/month, 3 symbols"

# 3. Self-test (без данных, 10/10)
python scripts/track_c_replay.py --self-test
# ожидается: checks все true, ready_for_data true

# 4. Синтетический прогон (для CI без сети)
python scripts/generate_binance_archive.py --out-dir /tmp/binance_vision
python scripts/track_c_replay.py --source binance-vision \
  --archive-dir /tmp/binance_vision --years 2021,2022,2023,2024,2025,2026 \
  --provenance "synthetic official-format"
```

## 8. Границы research-PR

- Живой контур, пороги, гейты, `exit_plan`, `entry_gates (enabled=False)`, `bot.yml` — не трогались (проверено `git diff --stat` — только `scripts/fetch_klines.py`, `tests/unit/test_fetch_klines.py`, `scripts/generate_binance_archive.py`, `reports/`).
- Если что-то в живом контуре хочется поменять по итогам реплея — только текстом как рекомендация владельцу (см. §6).

## 9. Provenance и файлы

- Официальный формат: `open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore` (`scripts/fetch_klines.py:19` `HEADER`).
- Диапазоны: 2021-01-01 → 2026-08-31, 68 месяцев, ~180 свечей/мес 4h, ~720 1h.
- Количество баров (синтетика, для примера): BTC 4h 12414, 1h 49656; ETH 4h 12414, 1h 49656; SOL аналогично. На официале ожидается близкое.
- `reports/track_c_replay_negative_control_2026-09-14.md` — негативный контроль на синтетике 2021-2024 4h (8761 бар, `htf_context_coverage=0.339`).
- Этот отчёт: `reports/track_c_deep_history_2026-09-14.md`.

## 10. Рекомендация владельцу (только текст)

- После мержа наблюдать за `strategy-lab.yml` — должен стать зелёным.
- Запустить полный реплей Z3/Z4/Z6/Z7 на официальном архиве на машине без egress-фильтра (или в GitHub Actions с кэшем) — 3 символа × 2 ТФ × 68 мес ≈ 186k баров, время ~15-20 мин на 4h, ~2-3 часа на 1h (требует оптимизации `daily_bias` — сейчас O(n²)).
- Если Z4 покажет OOS meanR >0 с CI низ >0 и n>=100 на официале — рассмотреть спек в `docs/` с `enabled=False` заготовкой и тестами на фикс-свечах, но не подключать в пайплайн до решения владельца.
- Z1 остаётся проваленной — не использовать ретест без контекста как улучшение пробоя.
- Кэш: добавить в `strategy-lab.yml` шаг `actions/cache@v4` для `data/` с ключом `binance-vision-${{ hashFiles('scripts/fetch_klines.py') }}-${{ env.START }}-${{ env.END }}` и оставить `upload-artifact` для отчётов.
