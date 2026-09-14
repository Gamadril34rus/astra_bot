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
- Фактически создана ветка `research/binance-data` с реальными CSV (после загрузки владельцем через UI) как пример кэша: `BTCUSDT_4h.csv 12414 баров`, `1h 49642`, размер 29 МБ за 6 файлов.

## 2. Данные и provenance

### 2.1 Официальный источник

- **Источник**: `https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/{tf}/{SYMBOL}-{tf}-YYYY-MM.zip` — официальные месячные архивы спота Binance, без API-ключа (`scripts/fetch_klines.py:14` `VISION_BASE`).
- **Диапазон**: `2021-01-01 → 2026-08-31` (68 месяцев, `2021-01` … `2026-08`), как в упавшем воркфлоу #4 (BTCUSDT 4h ~180 свечей/мес, данные годные).
- **Количество**: 68 мес × ~180 = ~12k баров 4h, 68 × ~720 = ~49k баров 1h на символ. Для 3 символов (BTC, ETH, SOL) × 2 ТФ ≈ 186k баров.

### 2.2 Реальные данные, загруженные владельцем (2026-09-14)

Владелец загрузил через GitHub UI в ветку `research/binance-data` 6 файлов (Add files via upload, commit `c09d35e`):

- `BTCUSDT_4h.csv` — 12415 строк (12414 свечей), первая `1609459200000,28923.63...` = 2021-01-01, последняя `1788206400000000,78898.06...` = 2026-08-31 (микросекунды в хвосте, парсер `_to_seconds` `track_c_replay.py:110-118` обрабатывает `>1e14` как мкс)
- `BTCUSDT_1h.csv` — 49643 строк (49642 свечи)
- `ETHUSDT_4h.csv` — 12415 строк, `1h` — 49643
- `SOLUSDT_4h.csv` — 12415 строк, `1h` — 49643

Provenance: `data.binance.vision official spot klines monthly 2021-01→2026-08, 68 months, BTCUSDT/ETHUSDT/SOLUSDT 1h/4h, real, 12414 4h / 49642 1h per symbol, ~180 4h/month`

Формат: `open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore` (`scripts/fetch_klines.py:19` `HEADER`).

Для реплея сконвертировано в `/tmp/binance_real/` как `BTCUSDT-4h-2021-01.csv` и т.д. (лоадер `load_binance_vision` `track_c_replay.py:167-174` ищет `**/{SYMBOL}-{TF}-YYYY-MM.(csv|zip)` рекурсивно, склейка, дедуп по `open_time`).

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

## 4. Результаты на глубокой истории — ОФИЦИАЛЬНЫЕ данные 4h 2021-01→2026-08

### 4.1 Команда

```bash
python scripts/track_c_replay.py --source binance-vision \
  --archive-dir /tmp/binance_real \
  --years 2021,2022,2023,2024,2025,2026 \
  --provenance "data.binance.vision official spot klines monthly 2021-01→2026-08, 68 months, BTCUSDT/ETHUSDT/SOLUSDT 4h, real, 12414 bars per symbol, ~180/month"
```

Время: 3 символа 4h = 37242 бара, ~17 мин (1044с) из-за O(n²) `daily_bias`. 1h (49642 бара/символ) таймаутит за 30 мин (1799с) — требует оптимизации `daily_bias`.

`htf_context_coverage` (доля баров с bull/bear контекстом, а не neutral/None):
- BTCUSDT 4h: **0.4147** (первые 200 дневных баров без контекста)
- ETHUSDT 4h: **0.3625**
- SOLUSDT 4h: **0.4094**
Т.е. ~36-41% баров имели контекст, остальное — разогрев ленты или neutral. Печатается чтобы «нет контекста» не читалось как «опровергнуто».

### 4.2 Pooled 3 символа 4h — OOS (главный разрез)

| Вариант | IS n | IS meanR | OOS n | OOS meanR | OOS PF | OOS medianR | OOS maxDD R | OOS MFE>=1R | OOS CI 95% meanR | метка | вердикт |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Z1 контроль** ретест без контекста | 1052 | -0.531 | 473 | **-0.351** | 0.64 | -1.21 | 174.6 | 0.48 | [-0.541,-0.150] | none | **провалена** |
| **Z3** контр-вход после ложного пробоя с закреплением | 112 | +0.206 | 50 | -0.038 | 0.93 | +0.15 | 5.76 | 0.40 | [-0.359,+0.298] | none | **провалена** |
| **Z4** overshoot третий экстремум с перехаем | 99 | +0.235 | 54 | -0.123 | 0.85 | -1.11 | 11.58 | 0.44 | [-0.635,+0.454] | none | **провалена** |
| **Z6** ретест + дневная лента EMA 20/50/100/200 | 256 | -0.578 | 108 | -0.276 | 0.71 | -1.22 | 39.28 | 0.41 | [-0.758,+0.363] | none | **провалена** |
| **Z7** вход у границы по первому закрытию в сторону | 51 | -0.041 | 32 | -0.227 | 0.71 | -1.11 | 11.59 | 0.44 | [-0.676,+0.235] | none | **провалена** |

Базлайн breakout OOS: n=202 meanR -0.052 PF 0.905 CI [-0.207,+0.108] — сама боевая версия пробоя шеи без края на этом окне.

### 4.3 По символам 4h OOS

**BTCUSDT 4h (12414 баров, split 8689):**
- Z1 n=177 meanR -0.324 PF 0.66 CI [-0.622,+0.020] none
- Z3 n=20 meanR -0.174 PF 0.69 CI [-0.618,+0.283] none
- Z4 n=28 meanR -0.216 PF 0.74 CI [-0.828,+0.482] none
- Z6 n=46 meanR -0.659 PF 0.27 CI [-0.971,-0.296] none, coverage 0.4147
- Z7 n=12 meanR -0.141 PF 0.81 CI [-0.878,+0.602] none

**ETHUSDT 4h:**
- Z1 n=152 meanR -0.368 PF 0.64 CI [-0.712,+0.035] none
- Z3 n=13 meanR +0.226 PF 1.50 CI [-0.4,+0.9] none
- Z4 n=16 meanR +0.12 PF 1.2 CI [-0.6,+0.8] none
- Z6 n=?? meanR -0.6 coverage 0.3625 none
- Z7 n=?? meanR ~-0.2 none

**SOLUSDT 4h:**
- Z1 n=144 meanR -0.35 PF 0.64
- Z3 n=17 meanR -0.05
- Z4 n=10 meanR -0.3
- Z6 n=?? coverage 0.4094 weak (единственный weak в разрезе, но pooled none)
- Z7 n=??

### 4.4 Сравнение с коротким окном — вердикт реванша

- **Z3**: короткое OOS +0.39R n=8 CI [-0.41,+1.29] weak → глубокое официальное 4h pooled -0.038R n=50 CI [-0.359,+0.298] — **развалилось**, подтверждён провал. На синтетике было -0.015R — схоже.
- **Z4**: короткое +0.50R n=10 CI [-0.52,+1.61] weak → глубокое официальное -0.123R n=54 CI [-0.635,+0.454] — **развалилось**. На синтетике BTC-only был +0.515R n=21 weak, но на официале нет.
- **Z6**: на коротком окне неизмерима (нужно >=200 дневных баров, `reports/track_c_replay_negative_control_2026-09-14.md:34` `htf_context_coverage=0.339`). На годах впервые измерима: coverage 0.36-0.41, но OOS meanR -0.276 pooled — **провалена** на официале, как и на синтетике (-0.424).
- **Z7**: новая гипотеза PR #78, на официале OOS -0.227R n=32 — **провалена**.
- **Z1 контроль**: короткое -0.55R n=77 CI [-0.87,-0.19] провалена → глубокое официальное -0.351R n=473 CI [-0.541,-0.150] — **подтверждение провала** ретеста без контекста. Если бы выжила — сигнал о смене режима, но не выжила.

**Итог реванша на официальном архиве 4h 2021-2026:** ни одна из Z3/Z4/Z6/Z7 не пережила OOS. Прошлые намёки +0.39/+0.50 не подтвердились на годах данных.

## 5. Вердикты по заданию (по официальному 4h)

| Гипотеза | OOS-confirmed / weak / провалена | Обоснование |
|---|---|---|
| Z1 контроль | **провалена** | OOS CI [-0.541,-0.150] целиком отрицателен, n=473, maxDD 174R |
| Z3 | **провалена** | OOS -0.038R n=50 CI [-0.359,+0.298], PF 0.93 |
| Z4 | **провалена** | OOS -0.123R n=54 CI [-0.635,+0.454], PF 0.85 |
| Z6 | **провалена** | Впервые измерима, coverage 0.36-0.41, но OOS -0.276R n=108 CI [-0.758,+0.363] |
| Z7 | **провалена** | OOS -0.227R n=32 CI [-0.676,+0.235] |

**Переживших OOS нет** → спеков в `docs/` и выключенных заготовок (`enabled=False`) не создаётся. Если бы Z3/Z4 выжили — спеки в `docs/track_c_Z3_Z4_spec.md` + тесты на фикс-свечах, в пайплайн НЕ подключать, `bot.yml` не трогать.

Запрещённые переносы (по прежнему): DCA/усреднение, повторный вход после снятия стопа, weekly MA50, отключение треугольников — в реплей не включались (`reports/track_c_zeus_setups.md:31`).

## 6. Что наблюдать после мержа

- Следующий запуск `strategy-lab.yml` по расписанию (понедельник 08:00 UTC) должен пройти без 404-ретраев на текущий месяц и без `ValueError` на пути. Проверить `coverage.json` в артефакте: `months_skipped` должен содержать `2026-09: skipped: month not closed yet`, `months_ok` ~68 для BTC 4h 2021-08.
- Логи `fetch_klines.py` должны показывать `skipped (month not closed yet)` для месяцев >= текущего, а не 4 ретрая.
- Если появится дыра >3 мес в середине — воркфлоу падает с `обнаружена дыра в данных: X месяцев подряд отсутствуют` — внятная ошибка.
- Реплей на официальных архивах: `htf_context_coverage` для Z6 ~0.36-0.41 для 5 лет, а не 0.
- Метрики Z3/Z4 на официале: OOS meanR -0.038/-0.123, PF <1, CI пересекает 0 — провал подтверждён. Если вдруг Z1 выживет на годах — сигнал о смене режима.
- Живой контур не трогается: если по итогам официального реплея захочется поменять пороги — только текстом как рекомендацию владельцу.

## 7. Воспроизведение

```bash
# 1. Официальный архив (на машине с доступом к data.binance.vision)
python scripts/fetch_klines.py --symbol BTCUSDT --timeframe 4h --start 2021-01-01 --end 2026-08-31 --out data/BTCUSDT_4h.csv
python scripts/fetch_klines.py --symbol BTCUSDT --timeframe 1h --start 2021-01-01 --end 2026-08-31 --out data/BTCUSDT_1h.csv
# аналогично ETHUSDT, SOLUSDT

# 2. Реплей на годах (4h — 17 мин, 1h — 30+ мин, требует оптимизации daily_bias)
python scripts/track_c_replay.py --source binance-vision \
  --archive-dir /tmp/binance_real \
  --years 2021,2022,2023,2024,2025,2026 \
  --provenance "data.binance.vision official spot klines monthly 2021-01→2026-08, 68 months, BTCUSDT/ETHUSDT/SOLUSDT 4h, real, 12414 bars per symbol, ~180/month"

# 3. Self-test (без данных, 10/10)
python scripts/track_c_replay.py --self-test
# checks все true, ready_for_data true

# 4. Синтетический прогон (для CI без сети)
python scripts/generate_binance_archive.py --out-dir /tmp/binance_vision
python scripts/track_c_replay.py --source binance-vision --archive-dir /tmp/binance_vision --years 2021,2022,2023,2024,2025,2026 --provenance "synthetic official-format"
```

## 8. Границы research-PR

- Живой контур, пороги, гейты, `exit_plan`, `entry_gates (enabled=False)`, `bot.yml` — не трогались (проверено `git diff --stat` — только `scripts/fetch_klines.py`, `tests/unit/test_fetch_klines.py`, `scripts/generate_binance_archive.py`, `reports/`).
- Если что-то в живом контуре хочется поменять по итогам реплея — только текстом как рекомендация владельцу (см. §6).

## 9. Provenance и файлы

- Официальный формат: `open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore` (`scripts/fetch_klines.py:19` `HEADER`).
- Диапазоны: 2021-01-01 → 2026-08-31, 68 месяцев, ~180 свечей/мес 4h, ~720 1h.
- Количество баров (официальные, проверено): BTC 4h 12414, 1h 49642; ETH 4h 12414, 1h 49642; SOL 4h 12414, 1h 49642.
- `reports/track_c_replay_negative_control_2026-09-14.md` — негативный контроль на синтетике 2021-2024 4h (8761 бар, `htf_context_coverage=0.339`).
- Этот отчёт: `reports/track_c_deep_history_2026-09-14.md` (обновлён реальными данными 2026-09-14).
- JSON реплея реального 4h: `/tmp/replay_real_4h.json` (28814 байт, seed 20260913, cost 0.3% RT, ribbon_source local replica).

## 10. Рекомендация владельцу (только текст)

- После мержа наблюдать за `strategy-lab.yml` — должен стать зелёным.
- 1h таймфрейм на 5 лет (49642 бара/символ) сейчас таймаутит за 30 мин из-за O(n²) `daily_bias` (`track_c_replay.py:120-143` — для каждого дня пересчёт EMA по всем предыдущим дням). Нужна оптимизация: кэшировать EMA или считать инкрементально.
- Z3/Z4/Z6/Z7 на официальном 4h 2021-2026 провалены — не внедрять. Прошлые намёки +0.39/+0.50 на коротком окне не подтвердились.
- Z1 остаётся проваленной — не использовать ретест без контекста как улучшение пробоя.
- Кэш: добавить в `strategy-lab.yml` шаг `actions/cache@v4` для `data/` с ключом `binance-vision-${{ hashFiles('scripts/fetch_klines.py') }}-${{ env.START }}-${{ env.END }}` и оставить `upload-artifact`.
