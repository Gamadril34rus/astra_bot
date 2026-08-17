# ASTRA: исследование подходов известных трейдеров

Этот документ не превращает исторические биографии в торговые сигналы. ASTRA использует опубликованные принципы как набор гипотез для статистической проверки на собственных данных.

## 1. Jesse Livermore: price action, trend, pivotal points

Публично описываемый подход Livermore строился вокруг подтверждения движения ценой, торговли в направлении рынка, pivot/pivotal points, пробоев и поведения цены/объёма после пробоя. Для long он искал подтверждённое движение выше значимого уровня, для short — подтверждённое движение ниже; при нарушении ожидаемого поведения позиция должна закрываться. Отдельно важны запрет усреднения убыточной позиции и контроль убытка.

Что исследовать ASTRA:
- breakout direction;
- distance from pivot/level;
- volume expansion on breakout;
- reaction/retest after breakout;
- continuation versus failed breakout;
- leader strength relative to market;
- expected move on 5m/15m/1h/4h/1d.

Источники: публичные материалы о правилах и методе Livermore.

## 2. George Soros: macro regime and reflexivity

Soros известен макро-подходом и поиском ситуаций, где рыночные ожидания и фундаментальная реальность расходятся. Для ASTRA это не готовый сигнал long/short, а гипотеза: крупные макро-факторы и изменение ожиданий могут менять режим рынка и усиливать движение.

Что исследовать:
- macro/news event;
- expected-versus-realized reaction;
- regime transition;
- BTC/ETH reaction and cross-asset propagation;
- persistence versus reversal after major information shocks.

## 3. Stanley Druckenmiller: dominant driver + conviction + fast invalidation

Публично описываемый подход Druckenmiller подчёркивает поиск главного драйвера текущего рынка, концентрацию при высокой уверенности и быстрый выход при ошибке. Для ASTRA это превращается в задачу определения dominant_market_driver и проверки того, действительно ли он объясняет большую часть движения.

Что исследовать:
- dominant driver;
- market regime;
- direction agreement across timeframes;
- breadth/correlation;
- volatility expansion;
- invalidation speed;
- payoff asymmetry.

## 4. Paul Tudor Jones: macro + technical timing + capital preservation

Публичные материалы о Jones подчёркивают сочетание макроанализа с техническими сигналами и первичность сохранения капитала. ASTRA должна исследовать не только направление, но и состояние риска: волатильность, экстремальные движения, корреляции и расстояние до invalidation.

Что исследовать:
- macro regime;
- trend/momentum;
- support/resistance;
- volatility regime;
- failed breakout;
- stop/invalidation distance;
- adverse excursion before favorable excursion.

## 5. Trend following / systematic principles

У системных trend-following подходов важны направление тренда, адаптация к волатильности, дисциплина выхода и способность переживать серию небольших убытков ради крупных движений. Для ASTRA это отдельный исследовательский baseline, а не обещание прибыльности.

Что исследовать:
- trend persistence;
- moving-average structure;
- breakout continuation;
- ATR/volatility-normalized movement;
- trailing exit behaviour;
- regime dependence.

## Что ASTRA НЕ должна копировать

Нельзя превращать эти подходы в примитивные правила вида `RSI > 70 = short` или `breakout = long`. Исторический метод каждого трейдера зависит от рынка, горизонта, ликвидности, исполнения и управления риском.

Вместо этого ASTRA строит гипотезы и проверяет их статистически.

## Long/Short decision research model

Каждое направление оценивается независимо:

`P(up | state, event, context)`
`P(down | state, event, context)`
`expected_return_up`
`expected_return_down`
`expected_adverse_excursion`
`expected_favorable_excursion`

Сигнал допустим только если преимущество устойчиво на нескольких временных срезах и прошло out-of-sample проверку.

## Риск

Ни один исторический трейдер не является доказательством будущей прибыльности. ASTRA не должна считать отсутствие убыточных сделок достижимой гарантией. Основной защитный принцип: сначала оценить вероятность и размер неблагоприятного движения, затем размер виртуальной позиции.
