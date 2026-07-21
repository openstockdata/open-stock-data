"""
TickFlow 数据获取器。

官方文档: https://docs.tickflow.org/zh-Hans
REST API 使用 x-api-key 请求头认证；免费服务只支持历史日 K。
"""

import logging
import os
from datetime import datetime, time as dt_time, timezone
from typing import Any, Optional

import pandas as pd
import requests

from .base import BaseFetcher, NETWORK_EXCEPTIONS
from .types import RealtimeSource, UnifiedRealtimeQuote, safe_float
from ..exceptions import AuthenticationError, DataFetchError, RateLimitError

_LOGGER = logging.getLogger(__name__)


class TickflowFetcher(BaseFetcher):
    """TickFlow REST API 数据获取器。"""

    name = "TickflowFetcher"
    priority = 0
    backend_group = "tickflow"

    DEFAULT_API_URL = "https://api.tickflow.org"
    DEFAULT_FREE_API_URL = "https://free-api.tickflow.org"
    TIMEOUT = 15

    def __init__(self):
        super().__init__()
        self._api_key = os.getenv("TICKFLOW_API_KEY", "").strip()
        configured_url = os.getenv("TICKFLOW_API_URL", "").strip()
        default_url = self.DEFAULT_API_URL if self._api_key else self.DEFAULT_FREE_API_URL
        self._base_url = (configured_url or default_url).rstrip("/")
        self._available = True
        if self._api_key:
            _LOGGER.info("TickFlow 初始化成功: %s", self._base_url)
        else:
            _LOGGER.info("未配置 TICKFLOW_API_KEY，TickFlow 仅启用免费日线服务: %s", self._base_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json,
                headers=self._headers(),
                timeout=self.TIMEOUT,
            )
        except NETWORK_EXCEPTIONS:
            raise
        except requests.exceptions.RequestException as exc:
            raise DataFetchError(f"TickFlow 请求失败: {exc}") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                f"TickFlow 限流: {response.text[:300]}",
                retry_after=int(retry_after) if retry_after and retry_after.isdigit() else 300,
                limit_type="unknown",
            )
        if response.status_code in (401, 403):
            raise AuthenticationError(f"TickFlow 认证或权限失败: {response.text[:300]}")
        if response.status_code >= 400:
            raise DataFetchError(f"TickFlow HTTP {response.status_code}: {response.text[:300]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise DataFetchError(f"TickFlow 响应不是 JSON: {response.text[:300]}") from exc
        if not isinstance(payload, dict):
            raise DataFetchError("TickFlow 响应格式异常")
        return payload

    def _convert_stock_code(self, stock_code: str) -> str:
        code = str(stock_code).strip().upper()
        if "." in code:
            return code

        if code.isdigit():
            if len(code) <= 5:
                return f"{code.zfill(5)}.HK"
            code = code.zfill(6)
            if code.startswith(("6", "9")):
                return f"{code}.SH"
            if code.startswith(("0", "2", "3", "1")):
                return f"{code}.SZ"
            if code.startswith(("4", "8")):
                return f"{code}.BJ"
            return f"{code}.SH"

        return f"{code}.US"

    @staticmethod
    def _date_to_ms(value: str, end_of_day: bool = False) -> int:
        date = datetime.strptime(value, "%Y%m%d").date()
        wall_time = dt_time.max if end_of_day else dt_time.min
        return int(datetime.combine(date, wall_time, tzinfo=timezone.utc).timestamp() * 1000)

    @staticmethod
    def _compact_to_frame(data: dict[str, Any]) -> pd.DataFrame:
        if not data:
            return pd.DataFrame()

        timestamps = data.get("timestamp") or []
        rows = len(timestamps)
        frame_data: dict[str, Any] = {"date": pd.to_datetime(timestamps, unit="ms", utc=True).date}
        for source_col, target_col in (
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("amount", "amount"),
        ):
            values = data.get(source_col) or []
            if len(values) < rows:
                values = [*values, *([None] * (rows - len(values)))]
            frame_data[target_col] = values[:rows]

        df = pd.DataFrame(frame_data)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        return df

    def _fetch_raw_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """获取前复权日线数据。"""
        return self._fetch_klines(stock_code, start_date, end_date, adjust="forward")

    def _fetch_raw_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """获取未复权日线数据。"""
        return self._fetch_klines(stock_code, start_date, end_date, adjust="none")

    def _fetch_klines(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        *,
        adjust: str,
    ) -> Optional[pd.DataFrame]:
        params = {
            "symbol": self._convert_stock_code(stock_code),
            "period": "1d",
            "count": 10000,
            "start_time": self._date_to_ms(start_date),
            "end_time": self._date_to_ms(end_date, end_of_day=True),
            "adjust": adjust,
        }
        payload = self._request("GET", "/v1/klines", params=params)
        df = self._compact_to_frame(payload.get("data") or {})
        return df if not df.empty else None

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        result_cols = ["date", "open", "high", "low", "close", "volume", "amount"]
        available_cols = [col for col in result_cols if col in df.columns]
        return df[available_cols].copy().sort_values("date", ascending=True)

    def _quote_from_payload(self, item: dict[str, Any]) -> Optional[UnifiedRealtimeQuote]:
        ext = item.get("ext") if isinstance(item.get("ext"), dict) else {}
        symbol = str(item.get("symbol") or "")
        if not symbol:
            return None
        code = symbol.split(".", 1)[0]
        price = safe_float(item.get("last_price"))
        pre_close = safe_float(item.get("prev_close"))
        change_amount = safe_float(ext.get("change_amount"))
        if change_amount is None and price is not None and pre_close is not None:
            change_amount = price - pre_close
        change_pct = safe_float(ext.get("change_pct"))
        if change_pct is not None:
            change_pct *= 100
        elif change_amount is not None and pre_close not in (None, 0):
            change_pct = change_amount / pre_close * 100
        amplitude = safe_float(ext.get("amplitude"))
        if amplitude is not None:
            amplitude *= 100
        turnover_rate = safe_float(ext.get("turnover_rate"))
        if turnover_rate is not None:
            turnover_rate *= 100

        return UnifiedRealtimeQuote(
            code=code,
            name=ext.get("name"),
            source=RealtimeSource.TICKFLOW,
            price=price,
            change_pct=change_pct,
            change_amount=change_amount,
            volume=safe_float(item.get("volume")),
            amount=safe_float(item.get("amount")),
            turnover_rate=turnover_rate,
            amplitude=amplitude,
            open_price=safe_float(item.get("open")),
            high=safe_float(item.get("high")),
            low=safe_float(item.get("low")),
            pre_close=pre_close,
        )

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """获取实时行情。免费服务不提供实时行情，未配置 API Key 时跳过。"""
        if not self._api_key:
            return None
        symbol = self._convert_stock_code(stock_code)
        payload = self._request("GET", "/v1/quotes", params={"symbols": symbol})
        data = payload.get("data") or []
        if not data:
            return None
        return self._quote_from_payload(data[0])

    def get_batch_realtime_quotes(self, stock_codes: list[str]) -> dict[str, UnifiedRealtimeQuote]:
        """批量获取实时行情。"""
        if not self._api_key or not stock_codes:
            return {}
        symbols = [self._convert_stock_code(code) for code in stock_codes]
        payload = self._request("POST", "/v1/quotes", json={"symbols": symbols})
        result: dict[str, UnifiedRealtimeQuote] = {}
        for item in payload.get("data") or []:
            quote = self._quote_from_payload(item)
            if quote:
                result[quote.code] = quote
        return result
