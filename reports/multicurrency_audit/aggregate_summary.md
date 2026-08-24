# Многовалютный аудит Multicurrency MTF (2021–2026)

- Символы: `BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT`
- Период: 2021-01-01 → 2026-08-22 (OOS c 2025-01-01)

## Базовые метрики (Baseline)

- Full Window: PF **0.68**, Return **-72.60%**, MaxDD **73.12%**, Trades **2717**
- In-Sample: PF **0.68**, Return **-72.60%**, MaxDD **73.12%**, Trades **2717**
- Out-Of-Sample: PF **0.0**, Return **+0.00%**, MaxDD **0.00%**, Trades **0**

## Ablation Studies (Влияние компонентов)

| Вариант | Profit Factor | Return % | Max Drawdown % | Trades |
|---|---|---|---|---|
| Baseline | 0.68 | -72.60% | 73.12% | 2717 |
| without_btc_gate | 0.68 | -80.70% | 81.07% | 3485 |
| without_volume_filter | 0.65 | -80.38% | 80.72% | 2938 |
| without_retest | 0.68 | -72.60% | 73.12% | 2717 |
| without_partial_trailing | 0.0 | -2.78% | 2.78% | 10 |