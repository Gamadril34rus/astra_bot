-- ASTRA BOT — PostgreSQL Initialization
-- Creates database schema

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS instruments (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    base_asset VARCHAR(20),
    quote_asset VARCHAR(20),
    min_quantity NUMERIC,
    min_notional NUMERIC,
    step_size NUMERIC,
    tick_size NUMERIC,
    price_precision INTEGER,
    quantity_precision INTEGER,
    trading_status VARCHAR(20) DEFAULT 'trading',
    fee_rate NUMERIC DEFAULT 0.001,
    is_active BOOLEAN DEFAULT TRUE,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(exchange, symbol)
);

CREATE TABLE IF NOT EXISTS candles (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    open_time BIGINT NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC,
    quote_volume NUMERIC,
    trades_count INTEGER,
    taker_buy_base_volume NUMERIC,
    taker_buy_quote_volume NUMERIC,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(exchange, symbol, timeframe, open_time)
);

CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    trade_id VARCHAR(100),
    price NUMERIC,
    quantity NUMERIC,
    side VARCHAR(10),
    timestamp BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(exchange, symbol, trade_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    side VARCHAR(10) NOT NULL,
    order_type VARCHAR(20) NOT NULL,
    quantity NUMERIC,
    price NUMERIC,
    stop_price NUMERIC,
    take_profit_price NUMERIC,
    status VARCHAR(30),
    client_order_id VARCHAR(100),
    exchange_order_id VARCHAR(100),
    filled_quantity NUMERIC DEFAULT 0,
    filled_price NUMERIC,
    filled_fees NUMERIC DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    filled_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    side VARCHAR(10) NOT NULL,
    quantity NUMERIC,
    entry_price NUMERIC,
    current_price NUMERIC,
    unrealized_pnl NUMERIC,
    realized_pnl NUMERIC,
    status VARCHAR(20),
    strategy_name VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS balances (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    asset VARCHAR(50) NOT NULL,
    free NUMERIC,
    locked NUMERIC,
    total NUMERIC,
    usdt_equivalent NUMERIC,
    last_update TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(exchange, asset)
);

CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(50) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    signal_type VARCHAR(20),
    side VARCHAR(10),
    entry_price NUMERIC,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    position_size NUMERIC,
    risk_amount NUMERIC,
    confidence FLOAT,
    ml_probability FLOAT,
    expected_value FLOAT,
    market_regime VARCHAR(30),
    status VARCHAR(30),
    rejection_reason VARCHAR(500),
    features JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS risk_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    description VARCHAR(500),
    current_value NUMERIC,
    limit_value NUMERIC,
    action_taken VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date DATE UNIQUE NOT NULL,
    equity NUMERIC,
    initial_capital NUMERIC,
    daily_pnl NUMERIC,
    daily_pnl_pct NUMERIC,
    total_pnl NUMERIC,
    total_pnl_pct NUMERIC,
    high_water_mark NUMERIC,
    drawdown NUMERIC,
    trades_count INTEGER,
    wins INTEGER,
    losses INTEGER,
    win_rate NUMERIC,
    profit_factor NUMERIC,
    exposure NUMERIC,
    available_capital NUMERIC,
    reserve NUMERIC,
    market_regime VARCHAR(50),
    volatility NUMERIC,
    risk_status VARCHAR(50),
    exchange_health VARCHAR(50),
    ml_health VARCHAR(50),
    system_health VARCHAR(50),
    errors_count INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    feature_hash VARCHAR(64),
    prediction FLOAT,
    probability FLOAT,
    confidence FLOAT,
    features JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS model_versions (
    version VARCHAR(50) PRIMARY KEY,
    model_type VARCHAR(50),
    training_date TIMESTAMP,
    features_used TEXT[],
    parameters JSONB,
    oos_performance JSONB,
    status VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100),
    strategy_name VARCHAR(50),
    start_date TIMESTAMP,
    end_date TIMESTAMP,
    initial_capital NUMERIC,
    final_capital NUMERIC,
    net_profit NUMERIC,
    profit_factor NUMERIC,
    max_drawdown NUMERIC,
    sharpe_ratio NUMERIC,
    total_trades INTEGER,
    parameters JSONB,
    config_snapshot JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(exchange, symbol, timeframe, open_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(exchange, symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_exchange_symbol ON orders(exchange, symbol);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_events_created ON risk_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ml_predictions_created ON ml_predictions(created_at DESC);
