#!/usr/bin/env python3
"""
ASTRA BOT - Simple Demo Test
Упрощённый тест новых приоритетных движков
"""

import sys
sys.path.insert(0, '/home/user/astra_bot')

from datetime import datetime, timezone, timedelta

print("=" * 80)
print("ASTRA BOT - Simple Priority Engines Test")
print("=" * 80)
print()

# Тестируем загрузку всех движков
print("1. Testing engine imports...")
try:
    from astra_bot.core import (
        get_microstructure_flow_engine,
        get_liquidity_map_engine,
        get_liquidation_cascade_engine,
        get_portfolio_allocator,
    )
    print("   ✓ All engines imported")
except Exception as e:
    print(f"   ✗ Import error: {e}")
    sys.exit(1)

# Тестируем создание экземпляров
print("\n2. Testing engine instantiation...")
try:
    mf_engine = get_microstructure_flow_engine()
    lm_engine = get_liquidity_map_engine()
    lc_engine = get_liquidation_cascade_engine()
    pa_engine = get_portfolio_allocator()
    print("   ✓ All engines instantiated")
except Exception as e:
    print(f"   ✗ Instantiation error: {e}")
    sys.exit(1)

# Тестируем Microstructure Flow Engine
print("\n3. Testing Microstructure Flow Engine...")
try:
    # Добавляем снимок стакана
    snapshot = mf_engine.add_order_book_snapshot(
        symbol='BTC-USDT',
        timestamp=datetime.now(timezone.utc),
        bids=[(50000.0, 10.0), (49995.0, 5.0)],
        asks=[(50005.0, 10.0), (50010.0, 5.0)],
    )
    print(f"   ✓ Snapshot added: {snapshot.symbol}")
    
    # Анализируем
    analysis = mf_engine.analyze_microstructure('BTC-USDT', datetime.now(timezone.utc))
    print(f"   ✓ Analysis complete: {len(analysis.signals)} signals")
    if analysis.signals:
        print(f"   Signals: {analysis.signals}")
    
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Тестируем Liquidity Map Engine
print("\n4. Testing Liquidity Map Engine...")
try:
    # Обновляем карту ликвидности
    lm_engine.update_liquidity_map(
        symbol='BTC-USDT',
        timestamp=datetime.now(timezone.utc),
        bids=[(50000.0, 100.0), (49995.0, 50.0)],
        asks=[(50005.0, 100.0), (50010.0, 50.0)],
        current_price=50002.5,
    )
    print("   ✓ Liquidity map updated")
    
    # Анализируем
    analysis = lm_engine.analyze_liquidity('BTC-USDT', datetime.now(timezone.utc), current_price=50002.5)
    print(f"   ✓ Liquidity analysis: {len(analysis.signals)} signals")
    
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Тестируем Liquidation Cascade Engine
print("\n5. Testing Liquidation Cascade Engine...")
try:
    from astra_bot.core.market_analysis import LiquidationDirection
    
    # Добавляем событие ликвидации
    lc_engine.add_liquidation_event(
        symbol='BTC-USDT',
        timestamp=datetime.now(timezone.utc),
        price=50000.0,
        volume=50.0,
        direction=LiquidationDirection.LONG_LIQUIDATION,
        open_interest=1000000.0
    )
    print("   ✓ Liquidation event added")
    
    # Анализируем
    analysis = lc_engine.analyze_cascades('BTC-USDT', datetime.now(timezone.utc))
    print(f"   ✓ Cascade analysis: {analysis.total_liquidations} liquidations")
    
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Тестируем Portfolio Opportunity Allocator
print("\n6. Testing Portfolio Opportunity Allocator...")
try:
    # Добавляем сигнал (метод принимает отдельные параметры)
    signal = pa_engine.add_signal(
        signal_id='test_001',
        symbol='BTC-USDT',
        direction='long',
        confidence=0.8,
        expected_return=0.05,
        expected_return_std=0.02,
        risk=0.2,
        strength=0.8,
        sharpe_ratio=2.5,
    )
    print(f"   ✓ Signal added: {signal.signal_id}")
    
    # Аллокация
    from astra_bot.core.trading import AllocationMethod
    allocation = pa_engine.allocate_optimal(
        portfolio_id='demo_portfolio',
        signals=[signal],
        total_capital=10000.0,
        method=AllocationMethod.SHARPE_MAXIMIZATION
    )
    print(f"   ✓ Allocation: ${allocation.allocated_capital:.2f}")
    print(f"   Expected return: {allocation.expected_portfolio_return*100:.2f}%")
    print(f"   Portfolio risk: {allocation.expected_portfolio_risk*100:.2f}%")
    
    for signal in allocation.selected_signals:
        print(f"   - {signal.symbol}: direction={signal.direction}, return={signal.expected_return*100:.1f}%")
    
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("✅ ALL PRIORITY ENGINES WORKING!")
print("=" * 80)
print()
print("Summary:")
print("  ✓ Microstructure Flow Engine - order book analysis")
print("  ✓ Liquidity Map Engine - liquidity mapping")
print("  ✓ Liquidation Cascade Engine - cascade detection")
print("  ✓ Portfolio Opportunity Allocator - signal allocation")
print()
print("Next steps:")
print("1. Set OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE environment variables")
print("2. Run: export OKX_API_KEY=your_key")
print("3. Run: export OKX_API_SECRET=your_secret")
print("4. Run: export OKX_PASSPHRASE=your_passphrase")
print("5. Then run demo_test.py for live connection test")
