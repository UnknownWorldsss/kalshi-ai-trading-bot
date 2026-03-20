#!/usr/bin/env python3
"""Debug script to trace decision making."""
import asyncio
import sys
sys.path.insert(0, '/root/.openclaw/workspace/kalshi-ai-trading-bot')

from src.config.settings import settings
from src.utils.database import DatabaseManager

async def debug_decision():
    db = DatabaseManager()
    
    # Check 1: Daily budget
    daily_cost = await db.get_daily_ai_cost()
    print(f"Daily AI cost: ${daily_cost:.3f} / ${settings.trading.daily_ai_budget}")
    
    # Get a sample market
    import sqlite3
    conn = sqlite3.connect('trading_system.db')
    cursor = conn.cursor()
    cursor.execute('SELECT market_id, title, volume, category FROM markets LIMIT 1')
    row = cursor.fetchone()
    print(f"\nSample market: {row}")
    
    # Check volume threshold
    print(f"Market volume: {row[2]}, threshold: {settings.trading.min_volume_for_ai_analysis}")
    
    # Check category
    print(f"Market category: {row[3]}")
    print(f"Excluded categories: {settings.trading.exclude_low_liquidity_categories}")
    
    # Check analysis count
    cursor.execute('SELECT COUNT(*) FROM market_analyses WHERE market_id = ?', (row[0],))
    count = cursor.fetchone()[0]
    print(f"Analysis count for this market: {count}")
    
    conn.close()

if __name__ == "__main__":
    asyncio.run(debug_decision())
