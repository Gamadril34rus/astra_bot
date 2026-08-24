# Многовалютный аудит Multicurrency MTF (2021–2026)

- Символы: `BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT`
- Период: 2021-01-01 → 2024-12-31 (OOS c 2024-01-01)

## Базовые метрики (Baseline)

- Full Window: PF **0.79**, Return **-32.89%**, MaxDD **37.30%**, Trades **1391**
- In-Sample: PF **0.84**, Return **-18.34%**, MaxDD **23.83%**, Trades **968**
- Out-Of-Sample: PF **0.67**, Return **-17.92%**, MaxDD **19.82%**, Trades **424**

## Ablation Studies (Влияние компонентов)

| Вариант | Profit Factor | Return % | Max Drawdown % | Trades |
|---|---|---|---|---|
| baseline | 0.79 | -32.89% | 37.30% | 1391 |
| without_btc_gate | 0.78 | -37.84% | 41.92% | 1587 |
| without_volume_filter | 0.78 | -41.63% | 44.64% | 1748 |
| without_retest | 0.78 | -34.38% | 38.25% | 1412 |
| without_partial_trailing | 0.0 | -14.06% | 14.06% | 55 |