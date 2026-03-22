"""
Kalshi API client for trading operations.
Handles authentication, market data, and trade execution.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from src.config.settings import settings
from src.utils.logging_setup import TradingLoggerMixin


class KalshiAPIError(Exception):
    """Custom exception for Kalshi API errors."""
    pass


class KalshiClient(TradingLoggerMixin):
    """
    Kalshi API client for automated trading.
    Handles authentication, market data retrieval, and trade execution.
    """
    
    def __init__(
        self, 
        api_key: Optional[str] = None, 
        private_key_path: str = None,
        max_retries: int = 5,
        backoff_factor: float = 0.5
    ):
        """
        Initialize Kalshi client.
        
        Args:
            api_key: Kalshi API key (Key ID from the API key generation)
            private_key_path: Path to private key file
            max_retries: Maximum number of retries for failed requests
            backoff_factor: Factor for exponential backoff
        """
        self.api_key = api_key or settings.api.kalshi_api_key
        self.base_url = settings.api.kalshi_base_url
        self.private_key_path = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
        self.private_key = None
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        
        # Load private key
        self._load_private_key()
        
        # HTTP client with timeouts
        self.client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
        )
        
        self.logger.info("Kalshi client initialized", api_key_length=len(self.api_key) if self.api_key else 0)
    
    def _load_private_key(self) -> None:
        """Load private key from file."""
        try:
            private_key_path = Path(self.private_key_path)
            if not private_key_path.exists():
                raise KalshiAPIError(f"Private key file not found: {self.private_key_path}")
            
            with open(private_key_path, 'rb') as f:
                self.private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None
                )
            self.logger.info("Private key loaded successfully")
        except Exception as e:
            self.logger.error("Failed to load private key", error=str(e))
            raise KalshiAPIError(f"Failed to load private key: {e}")
    
    def _sign_request(self, timestamp: str, method: str, path: str) -> str:
        """
        Sign request using RSA PSS signing method as per Kalshi API docs.
        
        Args:
            timestamp: Request timestamp in milliseconds
            method: HTTP method
            path: Request path
        
        Returns:
            Base64 encoded signature
        """
        # Create message to sign: timestamp + method + path
        message = timestamp + method.upper() + path
        message_bytes = message.encode('utf-8')
        
        try:
            # Sign using RSA PSS as per Kalshi documentation
            signature = self.private_key.sign(
                message_bytes,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256()
            )
            
            return base64.b64encode(signature).decode('utf-8')
        except Exception as e:
            self.logger.error("Failed to sign request", error=str(e))
            raise KalshiAPIError(f"Failed to sign request: {e}")
    
    async def _make_authenticated_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        require_auth: bool = True
    ) -> Dict[str, Any]:
        """
        Make authenticated request to Kalshi API with retry logic.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            json_data: JSON request body
            require_auth: Whether authentication is required
        
        Returns:
            API response data
        """
        # Prepare request
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Add authentication headers if required
        if require_auth:
            # Get current timestamp in milliseconds
            timestamp = str(int(time.time() * 1000))
            
            # Create signature
            signature = self._sign_request(timestamp, method, endpoint)
            
            headers.update({
                "KALSHI-ACCESS-KEY": self.api_key,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": signature
            })
        
        # Prepare body
        body = None
        if json_data:
            body = json.dumps(json_data, separators=(',', ':'))
        
        # Add query parameters to URL if present
        if params:
            query_string = urlencode(params)
            url = f"{url}?{query_string}"
        
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                self.logger.debug(
                    "Making API request",
                    method=method,
                    endpoint=endpoint,
                    has_auth=require_auth,
                    attempt=attempt + 1
                )
                
                # Add aggressive delay between requests to prevent 429s
                await asyncio.sleep(0.5)  # 500ms delay = max 2 requests/second
                
                response = await self.client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body if body else None
                )
                
                response.raise_for_status()
                return response.json()
                
            except httpx.HTTPStatusError as e:
                last_exception = e
                # Rate limit (429) or server errors (5xx) are worth retrying
                if e.response.status_code == 429 or e.response.status_code >= 500:
                    sleep_time = self.backoff_factor * (2 ** attempt)
                    self.logger.warning(
                        f"API request failed with status {e.response.status_code}. Retrying in {sleep_time:.2f}s...",
                        endpoint=endpoint,
                        attempt=attempt + 1
                    )
                    await asyncio.sleep(sleep_time)
                else:
                    # Don't retry on other client errors (e.g., 400, 401, 404)
                    error_msg = f"HTTP {e.response.status_code}: {e.response.text}"
                    self.logger.error("API request failed without retry", error=error_msg, endpoint=endpoint)
                    raise KalshiAPIError(error_msg)
            except Exception as e:
                last_exception = e
                self.logger.warning(f"Request failed with general exception. Retrying...", error=str(e), endpoint=endpoint)
                sleep_time = self.backoff_factor * (2 ** attempt)
                await asyncio.sleep(sleep_time)
        
        raise KalshiAPIError(f"API request failed after {self.max_retries} retries: {last_exception}")
    
    async def get_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        return await self._make_authenticated_request("GET", "/trade-api/v2/portfolio/balance")
    
    async def get_positions(self, ticker: Optional[str] = None) -> Dict[str, Any]:
        """Get portfolio positions."""
        params = {}
        if ticker:
            params["ticker"] = ticker
        return await self._make_authenticated_request("GET", "/trade-api/v2/portfolio/positions", params=params)
    
    async def get_fills(self, ticker: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        """Get order fills.""" 
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return await self._make_authenticated_request("GET", "/trade-api/v2/portfolio/fills", params=params)
    
    async def get_orders(self, ticker: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
        """Get orders."""
        params = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        return await self._make_authenticated_request("GET", "/trade-api/v2/portfolio/orders", params=params)
    
    async def get_markets(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
        status: Optional[str] = None,
        tickers: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Get markets data.
        
        Args:
            limit: Maximum number of markets to return
            cursor: Pagination cursor
            event_ticker: Filter by event ticker
            series_ticker: Filter by series ticker
            status: Filter by market status
            tickers: List of specific tickers to fetch
        
        Returns:
            Markets data
        """
        params = {"limit": limit}
        
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if tickers:
            params["tickers"] = ",".join(tickers)
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/markets", params=params, require_auth=True
        )
    
    async def get_market(self, ticker: str) -> Dict[str, Any]:
        """Get specific market data."""
        return await self._make_authenticated_request(
            "GET", f"/trade-api/v2/markets/{ticker}", require_auth=False
        )
    
    async def get_orderbook(self, ticker: str, depth: int = 100) -> Dict[str, Any]:
        """
        Get market orderbook.
        
        Args:
            ticker: Market ticker
            depth: Orderbook depth
        
        Returns:
            Orderbook data
        """
        params = {"depth": depth}
        return await self._make_authenticated_request(
            "GET", f"/trade-api/v2/markets/{ticker}/orderbook", params=params, require_auth=False
        )
    
    async def get_market_history(
        self,
        ticker: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Get market price history.
        
        Args:
            ticker: Market ticker
            start_ts: Start timestamp
            end_ts: End timestamp
            limit: Number of records to return
        
        Returns:
            Price history data
        """
        params = {"limit": limit}
        if start_ts:
            params["start_ts"] = start_ts
        if end_ts:
            params["end_ts"] = end_ts
        
        return await self._make_authenticated_request(
            "GET", f"/trade-api/v2/markets/{ticker}/history", params=params, require_auth=False
        )
    
    async def place_order(
        self,
        ticker: str,
        client_order_id: str,
        side: str,
        action: str,
        count: int,
        type_: str = "market",
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
        expiration_ts: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Place a trading order.
        
        Args:
            ticker: Market ticker
            client_order_id: Unique client order ID
            side: "yes" or "no"
            action: "buy" or "sell"
            count: Number of contracts
            type_: Order type ("market" or "limit")
            yes_price: Yes price in cents (for limit orders)
            no_price: No price in cents (for limit orders)
            expiration_ts: Order expiration timestamp
        
        Returns:
            Order response
        """
        order_data = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": side,
            "action": action,
            "count": count,
            "type": type_
        }
        
        if yes_price is not None:
            order_data["yes_price"] = yes_price
        if no_price is not None:
            order_data["no_price"] = no_price
        if expiration_ts:
            order_data["expiration_ts"] = expiration_ts
        
        return await self._make_authenticated_request(
            "POST", "/trade-api/v2/portfolio/orders", json_data=order_data
        )
    
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order."""
        return await self._make_authenticated_request(
            "DELETE", f"/trade-api/v2/portfolio/orders/{order_id}"
        )
    
    async def get_trades(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get trade history.
        
        Args:
            ticker: Filter by ticker
            limit: Maximum number of trades to return
            cursor: Pagination cursor
        
        Returns:
            Trades data
        """
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/trades", params=params
        )
    
    # =========================================================================
    # SERIES ENDPOINTS - For discovering all available market series
    # =========================================================================
    
    async def get_series_list(
        self,
        category: Optional[str] = None,
        tags: Optional[str] = None,
        include_volume: bool = True,
        min_updated_ts: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get list of all available series.
        
        A series represents a template for recurring events (e.g., "Monthly Jobs Report",
        "Weekly Initial Jobless Claims", "Daily Weather in NYC").
        
        Args:
            category: Filter by category (e.g., 'economics', 'politics', 'sports')
            tags: Filter by tags (comma-separated)
            include_volume: Include total volume traded across all events in series
            min_updated_ts: Filter series updated after this Unix timestamp
        
        Returns:
            Series list data
        """
        params = {"include_volume": include_volume}
        if category:
            params["category"] = category
        if tags:
            params["tags"] = tags
        if min_updated_ts:
            params["min_updated_ts"] = min_updated_ts
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/series", params=params, require_auth=False
        )
    
    async def get_series(self, series_ticker: str, include_volume: bool = True) -> Dict[str, Any]:
        """
        Get data about a specific series by its ticker.
        
        Args:
            series_ticker: Series ticker (e.g., 'KXCPI', 'KXFED')
            include_volume: Include total volume for the series
        
        Returns:
            Series data
        """
        params = {"include_volume": include_volume}
        return await self._make_authenticated_request(
            "GET", f"/trade-api/v2/series/{series_ticker}", params=params, require_auth=False
        )
    
    # =========================================================================
    # EXCHANGE ENDPOINTS - For exchange status and announcements
    # =========================================================================
    
    async def get_exchange_status(self) -> Dict[str, Any]:
        """
        Get current exchange status.
        
        Returns:
            Exchange status including trading_active, exchange_active
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/exchange/status", require_auth=False
        )
    
    async def get_exchange_announcements(self) -> Dict[str, Any]:
        """
        Get all exchange-wide announcements.
        
        Returns:
            List of announcements
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/exchange/announcements", require_auth=False
        )
    
    async def get_exchange_schedule(self) -> Dict[str, Any]:
        """
        Get exchange trading schedule.
        
        Returns:
            Trading hours and maintenance windows
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/exchange/schedule", require_auth=False
        )
    
    async def get_historical_cutoff(self) -> Dict[str, Any]:
        """
        Get historical cutoff timestamps.
        
        Markets/settlements before these timestamps must be accessed via /historical/ endpoints.
        
        Returns:
            Cutoff timestamps for markets, trades, and orders
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/historical/cutoff", require_auth=False
        )
    
    # =========================================================================
    # SEARCH ENDPOINTS - For discovering markets by category/tags
    # =========================================================================
    
    async def get_tags_by_categories(self) -> Dict[str, Any]:
        """
        Get tags organized by series categories.
        
        Returns:
            Mapping of categories to their associated tags
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/search/tags_by_categories", require_auth=False
        )
    
    async def get_filters_by_sport(self) -> Dict[str, Any]:
        """
        Get available filters organized by sport.
        
        Returns:
            Filtering options for each sport including scopes and competitions
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/search/filters_by_sport", require_auth=False
        )
    
    # =========================================================================
    # PORTFOLIO ENDPOINTS - Additional portfolio management
    # =========================================================================
    
    async def get_settlements(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        ticker: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get settlement history.
        
        Args:
            limit: Maximum number of settlements to return
            cursor: Pagination cursor
            ticker: Filter by market ticker
        
        Returns:
            Settlement history data
        """
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if ticker:
            params["ticker"] = ticker
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/settlements", params=params
        )
    
    async def get_fills(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        ticker: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get all fills (trades that were matched).
        
        Args:
            limit: Maximum number of fills to return
            cursor: Pagination cursor
            ticker: Filter by market ticker
            min_ts: Filter fills after this timestamp
            max_ts: Filter fills before this timestamp
        
        Returns:
            Fills data
        """
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if ticker:
            params["ticker"] = ticker
        if min_ts:
            params["min_ts"] = min_ts
        if max_ts:
            params["max_ts"] = max_ts
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/fills", params=params
        )
    
    # =========================================================================
    # HISTORICAL ENDPOINTS - For archived markets and data
    # =========================================================================
    
    async def get_historical_markets(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        tickers: Optional[List[str]] = None,
        event_ticker: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get markets that have been archived to the historical database.
        
        Markets settling before the historical cutoff are only available here.
        
        Args:
            limit: Maximum number of markets to return
            cursor: Pagination cursor
            tickers: List of specific tickers to fetch
            event_ticker: Filter by event ticker
        
        Returns:
            Historical markets data
        """
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if tickers:
            params["tickers"] = ",".join(tickers)
        if event_ticker:
            params["event_ticker"] = event_ticker
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/historical/markets", params=params, require_auth=False
        )
    
    async def get_historical_market(self, ticker: str) -> Dict[str, Any]:
        """
        Get data about a specific historical market by its ticker.
        
        Args:
            ticker: Market ticker
        
        Returns:
            Historical market data
        """
        return await self._make_authenticated_request(
            "GET", f"/trade-api/v2/historical/markets/{ticker}", require_auth=False
        )
    
    async def get_historical_candlesticks(
        self,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 1440
    ) -> Dict[str, Any]:
        """
        Get historical candlestick data for archived markets.
        
        Args:
            ticker: Market ticker
            start_ts: Start timestamp (Unix)
            end_ts: End timestamp (Unix)
            period_interval: Candlestick period in minutes (1, 60, or 1440)
        
        Returns:
            Candlestick data
        """
        params = {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": period_interval
        }
        return await self._make_authenticated_request(
            "GET", f"/trade-api/v2/historical/markets/{ticker}/candlesticks",
            params=params, require_auth=False
        )
    
    async def get_historical_fills(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        max_ts: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get historical fills (trades before the cutoff).
        
        Args:
            ticker: Filter by ticker
            limit: Maximum number of fills
            cursor: Pagination cursor
            max_ts: Filter fills before this timestamp
        
        Returns:
            Historical fills data
        """
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        if max_ts:
            params["max_ts"] = max_ts
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/historical/fills", params=params
        )
    
    async def get_historical_orders(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        max_ts: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get orders that have been archived to the historical database.
        
        Args:
            ticker: Filter by ticker
            limit: Maximum number of orders
            cursor: Pagination cursor
            max_ts: Filter orders before this timestamp
        
        Returns:
            Historical orders data
        """
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        if max_ts:
            params["max_ts"] = max_ts
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/historical/orders", params=params
        )
    
    async def get_historical_trades(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get all historical trades for all markets.
        
        Args:
            ticker: Filter by ticker
            limit: Maximum number of trades
            cursor: Pagination cursor
            min_ts: Filter trades after this timestamp
            max_ts: Filter trades before this timestamp
        
        Returns:
            Historical trades data
        """
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        if min_ts:
            params["min_ts"] = min_ts
        if max_ts:
            params["max_ts"] = max_ts
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/historical/trades", params=params, require_auth=False
        )
    
    # =========================================================================
    # BATCH ORDER ENDPOINTS - For managing multiple orders
    # =========================================================================
    
    async def batch_create_orders(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Submit a batch of orders (max 20 per batch).
        
        Args:
            orders: List of order data dictionaries
        
        Returns:
            Batch creation results
        """
        return await self._make_authenticated_request(
            "POST", "/trade-api/v2/portfolio/orders/batched",
            json_data={"orders": orders}
        )
    
    async def batch_cancel_orders(self, order_ids: List[str]) -> Dict[str, Any]:
        """
        Cancel up to 20 orders at once.
        
        Args:
            order_ids: List of order IDs to cancel
        
        Returns:
            Batch cancellation results
        """
        return await self._make_authenticated_request(
            "DELETE", "/trade-api/v2/portfolio/orders/batched",
            json_data={"order_ids": order_ids}
        )
    
    async def amend_order(
        self,
        order_id: str,
        count: Optional[int] = None,
        yes_price_dollars: Optional[float] = None,
        no_price_dollars: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Amend an existing order (change price/size without cancel+replace).
        
        Args:
            order_id: Order ID to amend
            count: New contract count
            yes_price_dollars: New yes price in dollars
            no_price_dollars: New no price in dollars
        
        Returns:
            Amended order data
        """
        json_data = {}
        if count is not None:
            json_data["count"] = count
        if yes_price_dollars is not None:
            json_data["yes_price_dollars"] = yes_price_dollars
        if no_price_dollars is not None:
            json_data["no_price_dollars"] = no_price_dollars
        
        return await self._make_authenticated_request(
            "POST", f"/trade-api/v2/portfolio/orders/{order_id}/amend",
            json_data=json_data
        )
    
    async def decrease_order(self, order_id: str, count: int) -> Dict[str, Any]:
        """
        Decrease the number of contracts in an existing order.
        
        Args:
            order_id: Order ID to decrease
            count: Number of contracts to decrease by
        
        Returns:
            Updated order data
        """
        return await self._make_authenticated_request(
            "POST", f"/trade-api/v2/portfolio/orders/{order_id}/decrease",
            json_data={"count": count}
        )
    
    async def get_order_queue_positions(
        self,
        market_tickers: Optional[List[str]] = None,
        event_ticker: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get queue positions for all resting orders.
        
        Queue position = number of contracts that need to be matched before
        this order receives a partial or full match (price-time priority).
        
        Args:
            market_tickers: Filter by specific market tickers
            event_ticker: Filter by event ticker
        
        Returns:
            Queue positions data
        """
        params = {}
        if market_tickers:
            params["market_tickers"] = ",".join(market_tickers)
        if event_ticker:
            params["event_ticker"] = event_ticker
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/orders/queue_positions", params=params
        )
    
    async def get_order_queue_position(self, order_id: str) -> Dict[str, Any]:
        """
        Get queue position for a single order.
        
        Args:
            order_id: Order ID
        
        Returns:
            Queue position data
        """
        return await self._make_authenticated_request(
            "GET", f"/trade-api/v2/portfolio/orders/{order_id}/queue_position"
        )
    
    # =========================================================================
    # ORDER GROUP ENDPOINTS - For managing order groups
    # =========================================================================
    
    async def get_order_groups(self) -> Dict[str, Any]:
        """
        Get all order groups for the authenticated user.
        
        Returns:
            List of order groups
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/order_groups"
        )
    
    async def create_order_group(
        self,
        name: str,
        contracts_limit: int,
        order_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Create a new order group with a contracts limit.
        
        When the limit is hit over a rolling 15-second window, all orders
        in the group are cancelled.
        
        Args:
            name: Group name
            contracts_limit: Max contracts per 15-second window
            order_ids: Initial order IDs to add to group
        
        Returns:
            Created order group data
        """
        json_data = {"name": name, "contracts_limit": contracts_limit}
        if order_ids:
            json_data["order_ids"] = order_ids
        
        return await self._make_authenticated_request(
            "POST", "/trade-api/v2/portfolio/order_groups/create",
            json_data=json_data
        )
    
    async def get_order_group(self, order_group_id: str) -> Dict[str, Any]:
        """
        Get details for a single order group.
        
        Args:
            order_group_id: Order group ID
        
        Returns:
            Order group details
        """
        return await self._make_authenticated_request(
            "GET", f"/trade-api/v2/portfolio/order_groups/{order_group_id}"
        )
    
    async def delete_order_group(self, order_group_id: str) -> Dict[str, Any]:
        """
        Delete an order group and cancel all orders within it.
        
        Args:
            order_group_id: Order group ID to delete
        
        Returns:
            Empty response
        """
        return await self._make_authenticated_request(
            "DELETE", f"/trade-api/v2/portfolio/order_groups/{order_group_id}"
        )
    
    async def reset_order_group(self, order_group_id: str) -> Dict[str, Any]:
        """
        Reset the order group's matched contracts counter to zero.
        
        Args:
            order_group_id: Order group ID
        
        Returns:
            Empty response
        """
        return await self._make_authenticated_request(
            "PUT", f"/trade-api/v2/portfolio/order_groups/{order_group_id}/reset"
        )
    
    async def trigger_order_group(self, order_group_id: str) -> Dict[str, Any]:
        """
        Trigger the order group (cancel all orders, prevent new orders).
        
        Args:
            order_group_id: Order group ID
        
        Returns:
            Empty response
        """
        return await self._make_authenticated_request(
            "PUT", f"/trade-api/v2/portfolio/order_groups/{order_group_id}/trigger"
        )
    
    async def update_order_group_limit(
        self,
        order_group_id: str,
        contracts_limit: int
    ) -> Dict[str, Any]:
        """
        Update the order group contracts limit.
        
        Args:
            order_group_id: Order group ID
            contracts_limit: New contracts limit
        
        Returns:
            Empty response
        """
        return await self._make_authenticated_request(
            "PUT", f"/trade-api/v2/portfolio/order_groups/{order_group_id}/limit",
            json_data={"contracts_limit": contracts_limit}
        )
    
    # =========================================================================
    # SUBACCOUNT ENDPOINTS - For managing subaccounts
    # =========================================================================
    
    async def create_subaccount(self) -> Dict[str, Any]:
        """
        Create a new subaccount (numbered sequentially starting from 1).
        
        Maximum 32 subaccounts per user.
        
        Returns:
            Created subaccount data
        """
        return await self._make_authenticated_request(
            "POST", "/trade-api/v2/portfolio/subaccounts"
        )
    
    async def transfer_between_subaccounts(
        self,
        from_account: int,
        to_account: int,
        amount_cents: int
    ) -> Dict[str, Any]:
        """
        Transfer funds between subaccounts.
        
        Use 0 for primary account, 1-32 for numbered subaccounts.
        
        Args:
            from_account: Source subaccount number
            to_account: Destination subaccount number
            amount_cents: Amount to transfer in cents
        
        Returns:
            Transfer result
        """
        return await self._make_authenticated_request(
            "POST", "/trade-api/v2/portfolio/subaccounts/transfer",
            json_data={
                "from_subaccount": from_account,
                "to_subaccount": to_account,
                "amount": amount_cents
            }
        )
    
    async def get_subaccount_balances(self) -> Dict[str, Any]:
        """
        Get balances for all subaccounts including primary.
        
        Returns:
            Subaccount balances
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/subaccounts/balances"
        )
    
    async def get_subaccount_transfers(
        self,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all transfers between subaccounts.
        
        Args:
            limit: Maximum number of transfers
            cursor: Pagination cursor
        
        Returns:
            Transfers data
        """
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/subaccounts/transfers", params=params
        )
    
    async def update_subaccount_netting(
        self,
        subaccount: int,
        netting_enabled: bool
    ) -> Dict[str, Any]:
        """
        Update netting enabled setting for a subaccount.
        
        Args:
            subaccount: Subaccount number (0 for primary)
            netting_enabled: Whether to enable netting
        
        Returns:
            Updated netting settings
        """
        return await self._make_authenticated_request(
            "PUT", "/trade-api/v2/portfolio/subaccounts/netting",
            json_data={
                "subaccount": subaccount,
                "netting_enabled": netting_enabled
            }
        )
    
    async def get_subaccount_netting(self) -> Dict[str, Any]:
        """
        Get netting enabled settings for all subaccounts.
        
        Returns:
            Netting settings
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/subaccounts/netting"
        )
    
    # =========================================================================
    # API KEY ENDPOINTS - For managing API keys
    # =========================================================================
    
    async def get_api_keys(self) -> Dict[str, Any]:
        """
        Get all API keys associated with the user.
        
        Returns:
            List of API keys
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/api_keys"
        )
    
    async def create_api_key(self, name: str, public_key: str) -> Dict[str, Any]:
        """
        Create a new API key with a user-provided public key.
        
        Requires Premier or Market Maker API usage level.
        
        Args:
            name: API key name
            public_key: RSA public key PEM string
        
        Returns:
            Created API key data
        """
        return await self._make_authenticated_request(
            "POST", "/trade-api/v2/api_keys",
            json_data={"name": name, "public_key": public_key}
        )
    
    async def generate_api_key(self, name: str) -> Dict[str, Any]:
        """
        Generate a new API key with an auto-created key pair.
        
        Returns the private key - store it securely, it cannot be retrieved again!
        
        Args:
            name: API key name
        
        Returns:
            Generated API key data including private_key
        """
        return await self._make_authenticated_request(
            "POST", "/trade-api/v2/api_keys/generate",
            json_data={"name": name}
        )
    
    async def delete_api_key(self, api_key_id: str) -> Dict[str, Any]:
        """
        Permanently delete an API key.
        
        Args:
            api_key_id: API key ID to delete
        
        Returns:
            Empty response
        """
        return await self._make_authenticated_request(
            "DELETE", f"/trade-api/v2/api_keys/{api_key_id}"
        )
    
    # =========================================================================
    # SERIES DATA ENDPOINTS - For series-specific market data
    # =========================================================================
    
    async def get_series_fee_changes(
        self,
        series_ticker: Optional[str] = None,
        show_historical: bool = False
    ) -> Dict[str, Any]:
        """
        Get fee changes for series.
        
        Args:
            series_ticker: Filter by specific series
            show_historical: Include historical fee changes
        
        Returns:
            Fee changes data
        """
        params = {"show_historical": show_historical}
        if series_ticker:
            params["series_ticker"] = series_ticker
        
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/series/fee_changes", params=params
        )
    
    async def get_market_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 1440
    ) -> Dict[str, Any]:
        """
        Get candlestick data for a market within a series.
        
        Args:
            series_ticker: Series ticker
            ticker: Market ticker
            start_ts: Start timestamp (Unix)
            end_ts: End timestamp (Unix)
            period_interval: Period in minutes (1, 60, or 1440)
        
        Returns:
            Candlestick data
        """
        params = {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": period_interval
        }
        return await self._make_authenticated_request(
            "GET",
            f"/trade-api/v2/series/{series_ticker}/markets/{ticker}/candlesticks",
            params=params, require_auth=False
        )
    
    # =========================================================================
    # ACCOUNT ENDPOINTS - For account management
    # =========================================================================
    
    async def get_account_limits(self) -> Dict[str, Any]:
        """
        Get API tier limits for the authenticated user.
        
        Returns:
            Account API limits
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/account/limits"
        )
    
    async def get_exchange_user_data_timestamp(self) -> Dict[str, Any]:
        """
        Get approximate timestamp when user data was last validated.
        
        Applies to: GetBalance, GetOrder(s), GetFills, GetPositions
        
        Returns:
            Timestamp data
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/exchange/user_data_timestamp"
        )
    
    # =========================================================================
    # PORTFOLIO SUMMARY ENDPOINTS
    # =========================================================================
    
    async def get_total_resting_order_value(self) -> Dict[str, Any]:
        """
        Get total value of resting orders in cents.
        
        Note: Only intended for FCM members.
        
        Returns:
            Total resting order value
        """
        return await self._make_authenticated_request(
            "GET", "/trade-api/v2/portfolio/summary/total_resting_order_value"
        )
    
    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
        self.logger.info("Kalshi client closed")
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close() 