#!/usr/bin/env python3
"""
Backfill missed BUY signals from market_analyses to paper_trades.

This script finds BUY decisions in the database that weren't logged as paper signals
due to the Position object bug, and adds them to the paper trading tracker.
"""

import sqlite3
import os
from datetime import datetime

# Connect to both databases
trading_db_path = 'trading_system.db'
paper_db_path = 'data/paper_trades.db'

def backfill_missed_signals():
    # Get existing paper signal market IDs
    conn_paper = sqlite3.connect(paper_db_path)
    cursor_paper = conn_paper.cursor()
    cursor_paper.execute("SELECT market_id FROM signals")
    existing_markets = {row[0] for row in cursor_paper.fetchall()}
    print(f"Existing paper signals: {len(existing_markets)}")
    
    # Get BUY decisions from trading database
    conn_trading = sqlite3.connect(trading_db_path)
    cursor_trading = conn_trading.cursor()
    cursor_trading.execute("""
        SELECT ma.market_id, m.title, ma.decision_action, ma.confidence, ma.analysis_timestamp
        FROM market_analyses ma
        JOIN markets m ON ma.market_id = m.market_id
        WHERE ma.decision_action = 'BUY'
        ORDER BY ma.analysis_timestamp ASC
    """)
    
    buy_decisions = cursor_trading.fetchall()
    print(f"Total BUY decisions in database: {len(buy_decisions)}")
    
    # Find missed signals
    missed = []
    for row in buy_decisions:
        market_id, title, action, confidence, timestamp = row
        if market_id not in existing_markets:
            missed.append({
                'market_id': market_id,
                'market_title': title or market_id,
                'side': 'YES',  # Default, would need more info for accurate side
                'entry_price': 0.5,  # Default
                'confidence': confidence or 0.6,
                'timestamp': timestamp or datetime.now().isoformat(),
                'reasoning': 'Backfilled from missed Position object signal',
                'strategy': 'directional'
            })
    
    print(f"Missed signals to backfill: {len(missed)}")
    
    if not missed:
        print("No missed signals to backfill!")
        conn_paper.close()
        conn_trading.close()
        return
    
    # Insert missed signals
    for signal in missed:
        cursor_paper.execute("""
            INSERT INTO signals 
            (market_id, market_title, side, entry_price, confidence, 
             timestamp, reasoning, strategy, outcome, pnl, settled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL)
        """, (
            signal['market_id'],
            signal['market_title'],
            signal['side'],
            signal['entry_price'],
            signal['confidence'],
            signal['timestamp'],
            signal['reasoning'],
            signal['strategy']
        ))
        print(f"  + Backfilled: {signal['market_title'][:50]}...")
    
    conn_paper.commit()
    conn_paper.close()
    conn_trading.close()
    
    print(f"\n✅ Backfilled {len(missed)} missed signals!")
    print(f"Total paper signals now: {len(existing_markets) + len(missed)}")

if __name__ == "__main__":
    backfill_missed_signals()
