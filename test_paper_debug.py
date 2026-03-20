#!/usr/bin/env python3
"""Quick test to debug paper trader signal logging."""

import asyncio
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.clients.kalshi_client import KalshiClient
from src.clients.xai_client import XAIClient
from src.utils.database import DatabaseManager
from src.jobs.decide import make_decision_for_market
from src.config.settings import settings
from src.paper.tracker import log_signal, get_stats

class PaperKalshiClient(KalshiClient):
    async def get_balance(self):
        return {"balance": 1000000, "portfolio_value": 0, "updated_ts": 1773967629}

async def test():
    print("🔍 Testing paper trader signal logging...")
    
    kalshi = PaperKalshiClient()
    db = DatabaseManager()
    xai = XAIClient(db_manager=db)
    
    from src.clients.model_router import ModelRouter
    model_router = ModelRouter(db_manager=db)
    
    # Get just a few markets
    markets = await db.get_eligible_markets(volume_min=settings.trading.min_volume, max_days_to_expiry=30)
    print(f"Found {len(markets)} markets")
    
    # Test first 3 markets
    for i, market in enumerate(markets[:3]):
        print(f"\n[{i+1}] Testing: {market.title[:50]}...")
        
        decision = await make_decision_for_market(
            market=market,
            db_manager=db,
            xai_client=xai,
            kalshi_client=kalshi,
            model_router=model_router,
        )
        
        print(f"  Decision: {decision}")
        
        if decision is None:
            print("  -> No decision (None)")
            continue
            
        action = decision.action if hasattr(decision, 'action') else decision.get("action", "skip")
        print(f"  -> Action: {action}")
        
        if action in ("skip", "hold", None):
            print("  -> Skipped")
            continue
            
        side = decision.side if hasattr(decision, 'side') else decision.get("side", "NO")
        confidence = decision.confidence if hasattr(decision, 'confidence') else decision.get("confidence", 0)
        limit_price = decision.limit_price if hasattr(decision, 'limit_price') else decision.get("limit_price", market.no_price)
        reasoning = decision.reasoning if hasattr(decision, 'reasoning') else decision.get("reasoning", "")
        
        print(f"  -> side={side}, confidence={confidence:.2f}, price={limit_price}")
        
        if confidence < 0.55:
            print(f"  -> SKIPPED: confidence {confidence:.2f} < 0.55")
            continue
        
        # Try to log the signal
        try:
            signal_id = log_signal(
                market_id=market.market_id,
                market_title=market.title,
                side=side,
                entry_price=limit_price,
                confidence=confidence,
                reasoning=reasoning,
                strategy="directional",
            )
            print(f"  ✅ Signal logged: ID {signal_id}")
        except Exception as e:
            print(f"  ❌ Error logging signal: {e}")
            import traceback
            traceback.print_exc()
    
    # Check stats
    stats = get_stats()
    print(f"\n📊 Final stats: {stats}")

if __name__ == "__main__":
    asyncio.run(test())
