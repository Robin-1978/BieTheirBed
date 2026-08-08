from __future__ import annotations

import httpx
import math
import re
from typing import Any

from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from pc_assistant.tools.http_limits import read_limited_json


_API_BASE = "https://api.frankfurter.dev/v1"
_MAX_EXCHANGE_RESPONSE_BYTES = 1024 * 1024
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


def _currency(value: Any, default: str) -> str | None:
    normalized = str(value or default).strip().upper()
    return normalized if _CURRENCY_CODE.fullmatch(normalized) else None


class ExchangeTool(ToolBase):
    name = "currency"
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.NETWORK})
    risk = ToolRisk.LOW
    description = "Convert between currencies."

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "rate")
        if action == "rate":
            return await self._get_rate(kwargs)
        elif action == "convert":
            return await self._convert(kwargs)
        elif action == "list":
            return await self._list_currencies()
        return {"error": f"Unknown action: {action}. Use 'rate', 'convert', or 'list'."}

    async def _get_rate(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        base = _currency(kwargs.get("base") or kwargs.get("from"), "USD")
        target = _currency(kwargs.get("target") or kwargs.get("to"), "CNY")
        if base is None or target is None:
            return {"error": "Currency codes must contain exactly three letters"}
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                async with client.stream(
                    "GET",
                    f"{_API_BASE}/latest?from={base}&to={target}",
                ) as resp:
                    resp.raise_for_status()
                    data = await read_limited_json(
                        resp,
                        _MAX_EXCHANGE_RESPONSE_BYTES,
                    )
        except Exception as e:
            return {"error": f"Failed to fetch exchange rate: {e}"}
        rates = data.get("rates", {})
        rate = rates.get(target)
        if rate is None:
            return {"error": f"Currency {target} not found"}
        return {
            "base": base,
            "target": target,
            "rate": rate,
            "date": data.get("date", ""),
            "description": f"1 {base} = {rate} {target}",
        }

    async def _convert(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        amount = kwargs.get("amount", 1)
        try:
            amount = float(amount)
        except (ValueError, TypeError):
            return {"error": f"Invalid amount: {amount}"}
        if not math.isfinite(amount) or abs(amount) > 1_000_000_000_000:
            return {"error": "Amount must be finite and at most 1000000000000"}
        base = _currency(kwargs.get("base") or kwargs.get("from"), "USD")
        target = _currency(kwargs.get("target") or kwargs.get("to"), "CNY")
        if base is None or target is None:
            return {"error": "Currency codes must contain exactly three letters"}
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                async with client.stream(
                    "GET",
                    f"{_API_BASE}/latest?amount={amount}&from={base}&to={target}",
                ) as resp:
                    resp.raise_for_status()
                    data = await read_limited_json(
                        resp,
                        _MAX_EXCHANGE_RESPONSE_BYTES,
                    )
        except Exception as e:
            return {"error": f"Failed to convert currency: {e}"}
        rates = data.get("rates", {})
        converted = rates.get(target)
        if converted is None:
            return {"error": f"Currency {target} not found"}
        return {
            "amount": amount,
            "base": base,
            "target": target,
            "converted": converted,
            "rate": converted / amount if amount != 0 else 0,
            "date": data.get("date", ""),
            "description": f"{amount} {base} = {converted} {target}",
        }

    async def _list_currencies(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                async with client.stream("GET", f"{_API_BASE}/currencies") as resp:
                    resp.raise_for_status()
                    return {
                        "currencies": await read_limited_json(
                            resp,
                            _MAX_EXCHANGE_RESPONSE_BYTES,
                        )
                    }
        except Exception as e:
            return {"error": f"Failed to list currencies: {e}"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["rate", "convert", "list"],
                        "description": "Action: 'rate' to get exchange rate, 'convert' to convert amount, 'list' to list currencies",
                    },
                    "base": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3}$",
                        "description": "Base currency code (e.g. 'USD', 'EUR', 'CNY')",
                    },
                    "target": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3}$",
                        "description": "Target currency code (e.g. 'CNY', 'JPY', 'USD')",
                    },
                    "amount": {
                        "type": "number",
                        "minimum": -1_000_000_000_000,
                        "maximum": 1_000_000_000_000,
                        "description": "Amount to convert (for 'convert' action, default: 1)",
                    },
                },
                "required": ["action"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["rate", "convert", "list"]},
                    "base": {"type": "string", "description": "3-letter code, e.g. USD"},
                    "target": {"type": "string", "description": "3-letter code, e.g. CNY"},
                    "amount": {"type": "number", "description": "for convert"},
                },
                "required": ["action"],
            },
        }
