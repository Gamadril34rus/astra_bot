#!/usr/bin/env python3
"""
ASTRA BOT - Demo Trading Test Script

Запуск тестирования на демо-счёте OKX с новыми приоритетными движками
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/astra_demo_test.log')
    ]
)
logger = logging.getLogger(__name__)

# Добавляем путь к модулям
sys.path.insert(0, '/home/user/astra_bot')


def load_config():
    """Загрузить конфигурацию"""
    from astra_bot.core.config import load_settings
    
    config_path = '/home/user/astra_bot/config/demo.yaml'
    if os.path.exists(config_path):
        logger.info(f"Loading demo config from {config_path}")
        return load_settings(config_path)
    
    logger.warning("Demo config not found, using production with sandbox mode")
    config = load_settings('/home/user/astra_bot/config/production.yaml')
    return config


async def test_priority_engines():
    """Тестирование новых приоритетных движков"""
    
    logger.info("=" * 80)
    logger.info("ASTRA BOT - Priority Engines Demo Test")
    logger.info("=" * 80)
    
    try:
        config = load_config()
        logger.info("✓ Configuration loaded")
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return False
    
    logger.info("\n--- Testing Priority Engines ---")
    
    # 1. Microstructure Flow Engine
    try:
        from astra_bot.core import get_microstructure_flow_engine
        mf_engine = get_microstructure_flow_engine()
        logger.info("✓ Microstructure Flow Engine loaded")
        
        # Добавляем снимок стакана
        snapshot = mf_engine.add_order_book_snapshot(
            symbol='BTC-USDT',
            timestamp=datetime.now(timezone.utc),
            bids=[(50000.0, 10.0), (49995.0, 5.0), (49990.0, 15.0)],
            asks=[(50005.0, 10.0), (50010.0, 5.0), (50015.0, 15.0)],
        )
        logger.info(f"  ✓ OrderBookSnapshot added: {snapshot.symbol}")
        
        # Анализируем
        analysis = mf_engine.analyze_microstructure('BTC-USDT')
        logger.info(f"  ✓ Microstructure analysis: {len(analysis.signals)} signals")
        
    except Exception as e:
        logger.error(f"✗ Microstructure Flow Engine error: {e}")
        return False
    
    # 2. Liquidity Map Engine
    try:
        from astra_bot.core import get_liquidity_map_engine
        lm_engine = get_liquidity_map_engine()
        logger.info("✓ Liquidity Map Engine loaded")
        
        # Добавляем уровни ликвидности
        from astra_bot.core.market_analysis import LiquidityLevel
        lm_engine.add_liquidity_level(LiquidityLevel(
            price=50000.0,
            volume=100.0,
            side='bid',
        ))
        lm_engine.add_liquidity_level(LiquidityLevel(
            price=50005.0,
            volume=150.0,
            side='ask',
        ))
        logger.info("  ✓ Liquidity levels added")
        
        # Анализируем
        analysis = lm_engine.analyze_liquidity('BTC-USDT')
        logger.info(f"  ✓ Liquidity analysis: {len(analysis.signals)} signals")
        
    except Exception as e:
        logger.error(f"✗ Liquidity Map Engine error: {e}")
        return False
    
    # 3. Liquidation Cascade Engine
    try:
        from astra_bot.core import get_liquidation_cascade_engine
        lc_engine = get_liquidation_cascade_engine()
        logger.info("✓ Liquidation Cascade Engine loaded")
        
        # Добавляем события ликвидации
        from astra_bot.core.market_analysis import LiquidationDirection
        lc_engine.add_liquidation_event(
            symbol='BTC-USDT',
            timestamp=datetime.now(timezone.utc),
            price=50000.0,
            volume=50.0,
            direction=LiquidationDirection.LONG_LIQUIDATION,
            open_interest=1000000.0
        )
        logger.info("  ✓ Liquidation event added")
        
        # Анализируем
        analysis = lc_engine.analyze_cascades('BTC-USDT', datetime.now(timezone.utc))
        logger.info(f"  ✓ Cascade analysis: {analysis.total_liquidations} liquidations")
        
    except Exception as e:
        logger.error(f"✗ Liquidation Cascade Engine error: {e}")
        return False
    
    # 4. Portfolio Opportunity Allocator
    try:
        from astra_bot.core import get_portfolio_allocator
        from astra_bot.core.trading import OpportunitySignal
        
        pa_engine = get_portfolio_allocator()
        logger.info("✓ Portfolio Opportunity Allocator loaded")
        
        # Создаём сигналы
        signals = [
            OpportunitySignal(
                signal_id='trend_001',
                symbol='BTC-USDT',
                direction='long',
                confidence=0.8,
                strength=0.8,
                expected_return=0.05,
                expected_return_std=0.02,
                sharpe_ratio=2.5,
                risk=0.2,
            ),
            OpportunitySignal(
                signal_id='trend_002',
                symbol='ETH-USDT',
                direction='long',
                confidence=0.7,
                strength=0.7,
                expected_return=0.04,
                expected_return_std=0.015,
                sharpe_ratio=2.0,
                risk=0.15,
            ),
        ]
        
        # Добавляем сигналы
        for signal in signals:
            pa_engine.add_signal(signal)
        logger.info(f"  ✓ {len(signals)} signals added")
        
        # Выполняем аллокацию
        allocation = pa_engine.allocate('sharpe_maximization', total_capital=10000.0)
        logger.info(f"  ✓ Portfolio allocation: {len(allocation.results)} positions")
        logger.info(f"    Total allocated: ${allocation.total_allocated:.2f}")
        logger.info(f"    Expected return: {allocation.expected_portfolio_return*100:.2f}%")
        
    except Exception as e:
        logger.error(f"✗ Portfolio Allocator error: {e}")
        return False
    
    logger.info("\n--- All Priority Engines Tested Successfully! ---")
    return True


async def test_okx_connection():
    """Тестирование подключения к OKX Demo"""
    
    logger.info("\n--- Testing OKX Demo Connection ---")
    
    try:
        from astra_bot.adapters.okx import OKXClient
        from astra_bot.core.config import load_settings
        
        # Загружаем конфиг
        config = load_settings('/home/user/astra_bot/config/demo.yaml')
        okx_config = config.exchanges.okx if hasattr(config, 'exchanges') else {}
        
        if not okx_config:
            logger.warning("OKX config not found, using defaults")
            okx_config = {
                'enabled': True,
                'sandbox': True,
                'api_key': os.getenv('OKX_API_KEY', ''),
                'api_secret': os.getenv('OKX_API_SECRET', ''),
                'passphrase': os.getenv('OKX_PASSPHRASE', ''),
                'contract_type': 'spot',
                'base_url': 'https://www.okx.com'
            }
        
        # Создаём клиент
        client = OKXClient(okx_config)
        
        # Инициализируем
        await client.initialize()
        logger.info("✓ OKX client initialized")
        
        # Тестируем соединение
        connected = await client.test_connection()
        if connected:
            logger.info("✓ OKX Demo connection successful!")
            
            # Пробуем получить инструменты
            instruments = await client.get_instruments("BTC-USDT")
            if instruments:
                logger.info(f"✓ Got {len(instruments)} instruments")
                for inst in instruments:
                    logger.info(f"  - {inst.symbol}: {inst.base_asset}/{inst.quote_asset}")
            
            # Пробуем получить баланс (в demo режиме)
            balances = await client.get_account_balance()
            logger.info(f"✓ Account balance retrieved: {len(balances)} assets")
            for asset, balance in list(balances.items())[:5]:
                logger.info(f"  - {asset}: {balance.total}")
            
            # Пробуем получить свечи
            candles = await client.get_candles("BTC-USDT", "1m", limit=10)
            if candles:
                logger.info(f"✓ Got {len(candles)} candles for BTC-USDT")
                last_candle = candles[-1]
                logger.info(f"  Last: O={last_candle.open}, H={last_candle.high}, "
                           f"L={last_candle.low}, C={last_candle.close}, V={last_candle.volume}")
            
            await client.close()
            return True
        else:
            logger.error("✗ OKX Demo connection failed")
            return False
            
    except Exception as e:
        logger.error(f"✗ OKX Demo connection error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def run_backtest_simulation():
    """Запуск симуляции бэктэстинга с новыми движками"""
    
    logger.info("\n--- Running Backtest Simulation ---")
    
    try:
        from astra_bot.core import (
            get_microstructure_flow_engine,
            get_liquidity_map_engine,
            get_liquidation_cascade_engine,
            get_portfolio_allocator,
        )
        from astra_bot.core.market_analysis import (
            LiquidityLevel,
            LiquidationDirection,
        )
        from astra_bot.core.trading import OpportunitySignal
        
        # Инициализируем движки
        mf_engine = get_microstructure_flow_engine()
        lm_engine = get_liquidity_map_engine()
        lc_engine = get_liquidation_cascade_engine()
        pa_engine = get_portfolio_allocator()
        
        logger.info("✓ All engines initialized")
        
        # Симулируем рыночные данные
        symbol = 'BTC-USDT'
        start_time = datetime.now(timezone.utc)
        
        # Генерация тестовых данных за 1 час
        for i in range(60):  # 60 минут
            current_time = start_time - timedelta(minutes=i)
            
            # Цена колеблется вокруг 50000
            base_price = 50000.0
            price_change = (i % 10) * 5  # ±50 от базовой цены
            current_price = base_price + price_change
            
            # Добавляем снимок стакана
            mf_engine.add_order_book_snapshot(
                symbol=symbol,
                timestamp=current_time,
                bids=[
                    (current_price - 5, 10.0),
                    (current_price - 10, 5.0),
                    (current_price - 15, 15.0),
                ],
                asks=[
                    (current_price + 5, 10.0),
                    (current_price + 10, 5.0),
                    (current_price + 15, 15.0),
                ],
            )
            
            # Добавляем уровни ликвидности
            lm_engine.add_liquidity_level(LiquidityLevel(
                price=current_price,
                volume=100.0 + (i % 20) * 5,
                side='bid' if i % 2 == 0 else 'ask',
            ))
            
            # Каждые 10 минут добавляем событие ликвидации
            if i % 10 == 0:
                lc_engine.add_liquidation_event(
                    symbol=symbol,
                    timestamp=current_time,
                    price=current_price,
                    volume=25.0 + (i % 3) * 10,
                    direction=LiquidationDirection.LONG_LIQUIDATION if i % 20 < 10 else LiquidationDirection.SHORT_LIQUIDATION,
                    open_interest=1000000.0 - i * 1000,
                )
        
        logger.info(f"✓ Generated 60 minutes of test data for {symbol}")
        
        # Анализируем данные
        mf_analysis = mf_engine.analyze_microstructure(symbol)
        lm_analysis = lm_engine.analyze_liquidity(symbol)
        lc_analysis = lc_engine.analyze_cascades(symbol, datetime.now(timezone.utc))
        
        logger.info(f"\n--- Analysis Results ---")
        logger.info(f"Microstructure: {len(mf_analysis.signals)} signals")
        logger.info(f"Liquidity: {len(lm_analysis.signals)} signals")
        logger.info(f"Cascades: {lc_analysis.total_liquidations} liquidations")
        
        # Создаём сигналы для аллокатора
        signals = [
            OpportunitySignal(
                signal_id='microstructure_001',
                symbol=symbol,
                direction='long' if mf_analysis.signals else 'neutral',
                confidence=0.85,
                strength=0.8,
                expected_return=0.05,
                expected_return_std=0.02,
                sharpe_ratio=2.5,
                risk=0.15,
            ),
        ]
        
        for signal in signals:
            pa_engine.add_signal(signal)
        
        # Аллокация
        allocation = pa_engine.allocate('sharpe_maximization', total_capital=10000.0)
        logger.info(f"\n--- Portfolio Allocation ---")
        logger.info(f"Total allocated: ${allocation.total_allocated:.2f}")
        logger.info(f"Expected return: {allocation.expected_portfolio_return*100:.2f}%")
        logger.info(f"Portfolio risk: {allocation.portfolio_risk*100:.2f}%")
        
        for result in allocation.results:
            logger.info(f"  {result.signal.symbol}: ${result.allocated_amount:.2f} "
                       f"({result.allocation_pct*100:.1f}%)")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Backtest simulation error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Главная функция"""
    
    logger.info("=" * 80)
    logger.info("ASTRA BOT - DEMO TRADING TEST")
    logger.info("Starting priority engines and OKX demo connection test...")
    logger.info("=" * 80)
    
    # Тестируем приоритетные движки
    engines_ok = await test_priority_engines()
    
    # Тестируем подключение к OKX Demo
    okx_ok = await test_okx_connection()
    
    # Запускаем симуляцию
    simulation_ok = await run_backtest_simulation()
    
    # Итоги
    logger.info("\n" + "=" * 80)
    logger.info("TEST RESULTS")
    logger.info("=" * 80)
    logger.info(f"Priority Engines: {'✓ PASS' if engines_ok else '✗ FAIL'}")
    logger.info(f"OKX Demo Connection: {'✓ PASS' if okx_ok else '✗ FAIL'}")
    logger.info(f"Backtest Simulation: {'✓ PASS' if simulation_ok else '✗ FAIL'}")
    
    if engines_ok and okx_ok and simulation_ok:
        logger.info("\n🎉 ALL TESTS PASSED! Ready for live demo trading!")
        return 0
    else:
        logger.error("\n❌ SOME TESTS FAILED! Check logs for details.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
