"""
Market Ingestion Job

This job fetches active markets from the Kalshi API, transforms them into a structured format,
and upserts them into the database.
"""
import asyncio
import time
from datetime import datetime
from typing import Optional, List

from src.clients.kalshi_client import KalshiClient
from src.utils.database import DatabaseManager, Market
from src.config.settings import settings
from src.utils.logging_setup import get_trading_logger


async def process_and_queue_markets(
    markets_data: List[dict],
    db_manager: DatabaseManager,
    queue: asyncio.Queue,
    existing_position_market_ids: set,
    logger,
):
    """
    Transforms market data, upserts to DB, and puts eligible markets on the queue.
    """
    markets_to_upsert = []
    for market_data in markets_data:
        # A simple approach is to take the average of bid and ask.
        # Kalshi API v2 uses dollar-denominated fields (yes_bid_dollars, yes_ask_dollars)
        # These are already in dollars (e.g., 0.5000 = $0.50)
        # Fall back to legacy cent-based fields (yes_bid, yes_ask) divided by 100
        if "yes_bid_dollars" in market_data:
            yes_bid = float(market_data.get("yes_bid_dollars", 0) or 0)
            yes_ask = float(market_data.get("yes_ask_dollars", 0) or 0)
            no_bid = float(market_data.get("no_bid_dollars", 0) or 0)
            no_ask = float(market_data.get("no_ask_dollars", 0) or 0)
            yes_price = (yes_bid + yes_ask) / 2
            no_price = (no_bid + no_ask) / 2
        else:
            # Legacy API: values in cents (0-100)
            yes_price = (market_data.get("yes_bid", 0) + market_data.get("yes_ask", 0)) / 2 / 100
            no_price = (market_data.get("no_bid", 0) + market_data.get("no_ask", 0)) / 2 / 100

        # Kalshi API v2 uses volume_fp (string/float) instead of volume (int)
        volume = int(float(market_data.get("volume_fp", 0) or market_data.get("volume", 0) or 0))
        
        # Skip markets with zero volume - they already exist in DB with real volume
        # or are not tradeable (provisional markets)
        if volume == 0:
            continue

        has_position = market_data["ticker"] in existing_position_market_ids
        
        # Derive category from series ticker since API no longer provides it
        ticker = market_data["ticker"]
        series = ticker.split('-')[0] if '-' in ticker else ticker
        
        category_map = {
            # Sports - NBA/NHL/MLB/etc
            'KXNBA': 'basketball', 'KXNHL': 'hockey', 'KXNFL': 'football',
            'KXCFB': 'college_football', 'KXMBL': 'baseball', 'KXWNB': 'wnba',
            'KXSOCCER': 'soccer', 'KXTEN': 'tennis', 'KXGOLF': 'golf',
            # Sports - Multivariate Events (MVE) - the main volume markets
            'KXMVESPORTSMULTIGAMEEXTENDED': 'sports_parlay',
            'KXMVECROSSCATEGORY': 'sports_parlay',
            'KXMVECBCHAMPIONSHIP': 'sports_parlay',
            'KXINTLFRIENDLYGAME': 'soccer',
            'KXDOTA2GAME': 'esports', 'KXCS2GAME': 'esports', 'KXLOLGAME': 'esports',
            'KXCBAGAME': 'basketball', 'KXKBLGAME': 'basketball',
            'KXWTAMATCH': 'tennis', 'KXRUGBYNRLMATCH': 'rugby',
            'KXNCAAMBSPREAD': 'basketball', 'KXMVENBASINGLEGAME': 'basketball',
            'KXLOLTOTALMAPS': 'esports', 'KXCS2MAP': 'esports', 'KXATPSETWINNER': 'tennis',
            # Crypto
            'KXBTC': 'crypto', 'KXETH': 'crypto', 'KXLTC': 'crypto', 'KXSUI': 'crypto',
            # Politics
            'KXTRUMP': 'politics', 'KXPARDON': 'politics', 'KXTREAS': 'politics',
            'KXPMJPN': 'politics', 'KXSECTREASURY': 'politics', 'KXEOCOUNT': 'politics',
            'KXG7': 'politics', 'KXISRAEL': 'politics', 'KXIRAN': 'politics',
            'KXXI': 'politics', 'KXXISUCCESSOR': 'politics', 'KXPERSON': 'politics',
            'KXNEXTISRAELPM': 'politics',
            # Pop Culture
            'KXELON': 'pop_culture', 'KXSPACEX': 'pop_culture', 'KXOSCARS': 'pop_culture',
            'KXGRAMMY': 'pop_culture', 'KXSONG': 'pop_culture', 'KXNEWPOPE': 'pop_culture',
            # Economics
            'KXCPIDATA': 'economics', 'KXJOBDATA': 'economics', 'KXRATEDATA': 'economics',
            'KXCPI': 'economics', 'KXFED': 'economics',
            # Weather/Science
            'KXSNOW': 'weather', 'KXTEMP': 'weather', 'KXWARMING': 'science',
            'KXMARS': 'science', 'KXCOLONIZE': 'science', 'KXERUPT': 'science',
            # Business
            'KXRAMP': 'business', 'KXOAI': 'business',
        }
        
        # Check for partial matches
        category = 'other'
        for prefix, cat in category_map.items():
            if series.startswith(prefix):
                category = cat
                break
        
        market = Market(
            market_id=ticker,
            title=market_data["title"],
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            expiration_ts=int(
                datetime.fromisoformat(
                    market_data["expiration_time"].replace("Z", "+00:00")
                ).timestamp()
            ),
            category=category,
            status=market_data["status"],
            last_updated=datetime.now(),
            has_position=has_position,
        )
        markets_to_upsert.append(market)

    if markets_to_upsert:
        await db_manager.upsert_markets(markets_to_upsert)
        logger.info(f"Successfully upserted {len(markets_to_upsert)} markets.")

        # Primary filtering criteria - MORE PERMISSIVE FOR MORE OPPORTUNITIES!
        min_volume: float = 100.0  # DECREASED: Much lower volume threshold (was 100, keeping low)
        min_volume_for_ai_analysis: float = 150.0  # DECREASED: Lower volume for AI analysis (was 200, now 150)  
        preferred_categories: List[str] = []  # Empty = all categories allowed
        excluded_categories: List[str] = []  # Empty = no categories excluded

        # Enhanced filtering for better opportunities - MORE PERMISSIVE FOR MORE TRADES
        min_price_movement: float = 0.015  # DECREASED: Even lower minimum range (was 0.02, now 1.5¢)
        max_bid_ask_spread: float = 0.20   # INCREASED: Allow even wider spreads (was 0.15, now 20¢)
        min_confidence_for_long_term: float = 0.40  # DECREASED: Lower confidence required (was 0.5, now 40%)

        eligible_markets = [
            m
            for m in markets_to_upsert
            if m.volume >= min_volume
            # REMOVED TIME RESTRICTION - we can now trade markets with ANY deadline!
            # Dynamic exit strategies will handle timing automatically
            and (
                not settings.trading.preferred_categories
                or m.category in settings.trading.preferred_categories
            )
            and m.category not in settings.trading.excluded_categories
        ]

        logger.info(
            f"Found {len(eligible_markets)} eligible markets to process in this batch."
        )
        
        # Sort by expiration time (soonest first) to prioritize markets that settle soon
        eligible_markets.sort(key=lambda m: m.expiration_ts)
        
        for market in eligible_markets:
            await queue.put(market)

    else:
        logger.info("No new markets to upsert in this batch.")


async def run_ingestion(
    db_manager: DatabaseManager,
    queue: asyncio.Queue,
    market_ticker: Optional[str] = None,
):
    """
    Main function for the market ingestion job.

    Args:
        db_manager: DatabaseManager instance.
        queue: asyncio.Queue to put ingested markets into.
        market_ticker: Optional specific market ticker to ingest.
    """
    logger = get_trading_logger("market_ingestion")
    logger.info("Starting market ingestion job.", market_ticker=market_ticker)

    kalshi_client = KalshiClient()

    try:
        # Get all market IDs with existing positions
        existing_position_market_ids = await db_manager.get_markets_with_positions()

        if market_ticker:
            logger.info(f"Fetching single market: {market_ticker}")
            market_response = await kalshi_client.get_market(ticker=market_ticker)
            if market_response and "market" in market_response:
                await process_and_queue_markets(
                    [market_response["market"]],
                    db_manager,
                    queue,
                    existing_position_market_ids,
                    logger,
                )
            else:
                logger.warning(f"Could not find market with ticker: {market_ticker}")
        else:
            logger.info("Fetching all active markets from Kalshi API with pagination.")
            
            # Fetch from events endpoint to get high-volume non-sports markets
            logger.info("Fetching markets from events endpoint...")
            try:
                events_response = await kalshi_client._make_authenticated_request(
                    'GET', '/trade-api/v2/events'
                )
                events = events_response.get('events', [])
                logger.info(f"Found {len(events)} events")
                
                # Fetch markets from each event (prioritize non-sports)
                event_markets_fetched = 0
                for event in events:
                    event_ticker = event.get('event_ticker')
                    if not event_ticker:
                        continue
                    
                    try:
                        response = await kalshi_client.get_markets(
                            limit=20, event_ticker=event_ticker
                        )
                        markets = response.get('markets', [])
                        
                        # Filter for active markets WITH volume
                        active = [
                            m for m in markets
                            if m['status'] == 'active'
                            and float(m.get('volume_fp', 0) or 0) > 0
                        ]
                        
                        if active:
                            await process_and_queue_markets(
                                active,
                                db_manager,
                                queue,
                                existing_position_market_ids,
                                logger,
                            )
                            event_markets_fetched += len(active)
                    except Exception as e:
                        logger.warning(f"Failed to fetch markets for event {event_ticker}: {e}")
                
                logger.info(f"Fetched {event_markets_fetched} markets from events endpoint")
            except Exception as e:
                logger.warning(f"Failed to fetch from events endpoint: {e}")
            
            # Fetch from historical endpoint for near-term markets with volume
            logger.info("Fetching markets from historical endpoint...")
            try:
                historical_response = await kalshi_client._make_authenticated_request(
                    'GET', '/trade-api/v2/historical/markets?limit=200'
                )
                historical_markets = historical_response.get('markets', [])
                logger.info(f"Found {len(historical_markets)} markets in historical endpoint")
                
                # Filter for markets with volume that haven't expired yet
                now_ts = int(datetime.now().timestamp())
                active_historical = []
                for m in historical_markets:
                    try:
                        exp_ts = int(datetime.fromisoformat(
                            m.get('expiration_time', '2099-01-01').replace('Z', '+00:00')
                        ).timestamp())
                        if exp_ts > now_ts and float(m.get('volume_fp', 0) or 0) > 0:
                            active_historical.append(m)
                    except:
                        pass
                
                if active_historical:
                    logger.info(f"Found {len(active_historical)} active historical markets with volume")
                    await process_and_queue_markets(
                        active_historical,
                        db_manager,
                        queue,
                        existing_position_market_ids,
                        logger,
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch from historical endpoint: {e}")
            
            # Fetch ECONOMIC series markets directly (CPI, Fed, etc.)
            logger.info("Fetching economic series markets (CPI, Fed, GDP, etc.)...")
            economic_series = [
                'KXCPI', 'KXCPIYOY', 'KXFED', 'KXFEDDECISION', 
                'KXU3', 'KXGDP', 'KXADP', 'KXPAYROLLS',
                'KXECB', 'KXWTI', 'KXINX', 'KXEGGS'
            ]
            
            economic_markets_fetched = 0
            for series in economic_series:
                try:
                    response = await kalshi_client.get_markets(
                        limit=50, series_ticker=series
                    )
                    markets = response.get('markets', [])
                    
                    if markets:
                        logger.info(f"  {series}: {len(markets)} markets")
                        # Process all markets (even 0 volume - DB may have data)
                        await process_and_queue_markets(
                            markets,
                            db_manager,
                            queue,
                            existing_position_market_ids,
                            logger,
                        )
                        economic_markets_fetched += len(markets)
                except Exception as e:
                    logger.debug(f"No markets for series {series}: {e}")
            
            logger.info(f"Fetched {economic_markets_fetched} markets from economic series")
            
            # Also fetch POLITICS series markets
            logger.info("Fetching politics series markets...")
            politics_series = [
                'KXTRUMP', 'KXPARDON', 'KXTREAS', 'KXCR',
                'KXPMJPN', 'KXSECTREASURY', 'KXEOCOUNT',
                'KXG7', 'KXISRAEL', 'KXIRAN', 'KXXI', 'KXXISUCCESSOR'
            ]
            
            politics_markets_fetched = 0
            for series in politics_series:
                try:
                    response = await kalshi_client.get_markets(
                        limit=30, series_ticker=series
                    )
                    markets = response.get('markets', [])
                    
                    if markets:
                        logger.info(f"  {series}: {len(markets)} markets")
                        await process_and_queue_markets(
                            markets,
                            db_manager,
                            queue,
                            existing_position_market_ids,
                            logger,
                        )
                        politics_markets_fetched += len(markets)
                except Exception as e:
                    logger.debug(f"No markets for series {series}: {e}")
            
            logger.info(f"Fetched {politics_markets_fetched} markets from politics series")
            
            # Also fetch near-term markets using settlement_before filter
            logger.info("Fetching near-term markets with settlement_before filter...")
            try:
                from datetime import timedelta
                soon = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
                near_term_response = await kalshi_client._make_authenticated_request(
                    'GET', f'/trade-api/v2/markets?limit=200&settlement_before={soon}'
                )
                near_term_markets = near_term_response.get('markets', [])
                logger.info(f"Found {len(near_term_markets)} near-term markets settling before {soon}")
                
                # Process all near-term markets (even with 0 volume - they may have data in DB)
                now_ts = int(datetime.now().timestamp())
                active_near_term = []
                for m in near_term_markets:
                    try:
                        exp_ts = int(datetime.fromisoformat(
                            m.get('expiration_time', '2099-01-01').replace('Z', '+00:00')
                        ).timestamp())
                        # Include if not expired and has any activity indicators
                        if exp_ts > now_ts:
                            vol = float(m.get('volume_fp', 0) or 0)
                            has_bid = float(m.get('yes_bid_dollars', 0) or 0) > 0
                            if vol > 0 or has_bid:
                                active_near_term.append(m)
                    except:
                        pass
                
                if active_near_term:
                    logger.info(f"Found {len(active_near_term)} active near-term markets")
                    await process_and_queue_markets(
                        active_near_term,
                        db_manager,
                        queue,
                        existing_position_market_ids,
                        logger,
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch near-term markets: {e}")
            cursor = None
            while True:
                response = await kalshi_client.get_markets(limit=100, cursor=cursor)
                markets_page = response.get('markets', [])

                # Filter for active markets WITH volume/liquidity
                active_markets = [
                    m for m in markets_page 
                    if m["status"] == "active"
                    and (
                        float(m.get("volume_fp", 0) or 0) > 0
                        or float(m.get("liquidity_dollars", 0) or 0) > 0
                        or float(m.get("yes_bid_dollars", 0) or 0) > 0
                    )
                ]
                if active_markets:
                    logger.info(
                        f"Fetched {len(markets_page)} markets, {len(active_markets)} are active with volume."
                    )
                    await process_and_queue_markets(
                        active_markets,
                        db_manager,
                        queue,
                        existing_position_market_ids,
                        logger,
                    )

                cursor = response.get("cursor")
                if not cursor:
                    break
            
            # Also fetch from diverse non-sports series
            diverse_series = [
                # Politics
                'KXTRUMPPARDON', 'KXSECTREASURY', 'KXNEXTJPNPM', 'KXEOCOUNTDAY2',
                # Crypto
                'KXLTC', 'KXSUIATH26', 'KXBTC', 'KXETH',
                # Pop culture
                'KXSONGRELEASEDATEHS', 'KXOSCARS', 'KXGRAMMY',
            ]
            
            for series in diverse_series:
                try:
                    response = await kalshi_client.get_markets(limit=50, series_ticker=series)
                    markets = response.get("markets", [])
                    # Filter for active markets WITH volume/liquidity
                    active = [
                        m for m in markets
                        if m["status"] == "active"
                        and (
                            float(m.get("volume_fp", 0) or 0) > 0
                            or float(m.get("liquidity_dollars", 0) or 0) > 0
                            or float(m.get("yes_bid_dollars", 0) or 0) > 0
                        )
                    ]
                    if active:
                        logger.info(f"Fetched {len(active)} markets with volume from series {series}")
                        await process_and_queue_markets(
                            active,
                            db_manager,
                            queue,
                            existing_position_market_ids,
                            logger,
                        )
                except Exception as e:
                    logger.warning(f"Failed to fetch series {series}: {e}")

    except Exception as e:
        logger.error(
            "An error occurred during market ingestion.", error=str(e), exc_info=True
        )
    finally:
        await kalshi_client.close()
        logger.info("Market ingestion job finished.")
