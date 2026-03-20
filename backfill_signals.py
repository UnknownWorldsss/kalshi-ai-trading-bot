#!/usr/bin/env python3
"""
Backfill paper trading signals from market_analyses table.

This script finds all BUY decisions in the market_analyses table
that aren't already in the paper_trading_signals table and adds them.
"""

import sqlite3
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.paper.tracker import log_signal, DB_PATH

def backfill_signals():
    """Backfill BUY signals from market_analyses to paper_trading_signals."""
    # Get BUY decisions from main trading database
    main_db_path = "trading_system.db"
    main_conn = sqlite3.connect(main_db_path)
    main_cursor = main_conn.cursor()
    
    # Get all BUY decisions from market_analyses
    main_cursor.execute("""
        SELECT DISTINCT ma.market_id, ma.confidence, m.title, m.yes_price, m.no_price
        FROM market_analyses ma
        JOIN markets m ON ma.market_id = m.market_id
        WHERE ma.decision_action = 'BUY'
        ORDER BY ma.analysis_timestamp DESC
    """)
    
    buy_decisions = main_cursor.fetchall()
    print(f"Found {len(buy_decisions)} BUY decisions in market_analyses")
    
    # Get existing paper signals from paper trades database
    paper_conn = sqlite3.connect(DB_PATH)
    paper_cursor = paper_conn.cursor()
    paper_cursor.execute("SELECT market_id FROM signals")
    existing_markets = {row[0] for row in paper_cursor.fetchall()}
    print(f"Found {len(existing_markets)} existing paper signals")
    
    # Backfill missing signals
    added = 0
    for market_id, confidence, title, yes_price, no_price in buy_decisions:
        if market_id in existing_markets:
            print(f"  Skipping (already exists): {market_id[:40]}...")
            continue
        
        # Determine side from the analysis (default to YES if can't tell)
        side = "YES"  # Default - you may want to look at the actual analysis data
        entry_price = yes_price if side == "YES" else no_price
        
        try:
            signal_id = log_signal(
                market_id=market_id,
                market_title=title or market_id,
                side=side,
                entry_price=entry_price or 0.5,
                confidence=confidence,
                reasoning="Backfilled from market_analyses BUY decision",
                strategy="directional"
            )
            print(f"  Added signal #{signal_id}: {side} {title[:50]}... (conf={confidence})")
            added += 1
        except Exception as e:
            print(f"  Error adding {market_id}: {e}")
    
    main_conn.close()
    paper_conn.close()
    print(f"\n✅ Backfill complete: Added {added} new paper trading signals")
    return added

if __name__ == "__main__":
    backfill_signals()
