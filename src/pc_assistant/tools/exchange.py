from __future__ import annotations

import httpx
from typing import Any

from pc_assistant.tools.base import ToolBase


_API_BASE = "https://api.frankfurter.dev/v1"


class ExchangeTool(ToolBase):
    name = "currency"
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
        base = (kwargs.get("base") or kwargs.get("from") or "USD").upper()
        target = (kwargs.get("target") or kwargs.get("to") or "CNY").upper()
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(f"{_API_BASE}/latest?from={base}&to={target}")
                resp.raise_for_status()
                data = resp.json()
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
        base = (kwargs.get("base") or kwargs.get("from") or "USD").upper()
        target = (kwargs.get("target") or kwargs.get("to") or "CNY").upper()
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(f"{_API_BASE}/latest?amount={amount}&from={base}&to={target}")
                resp.raise_for_status()
                data = resp.json()
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
                resp = await client.get(f"{_API_BASE}/currencies")
                resp.raise_for_status()
                return {"currencies": resp.json()}
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
                        "description": "Base currency code (e.g. 'USD', 'EUR', 'CNY')",
                    },
                    "target": {
                        "type": "string",
                        "description": "Target currency code (e.g. 'CNY', 'JPY', 'USD')",
                    },
                    "amount": {
                        "type": "number",
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
                    "amount": {"type": "number"},
                    "from": {"type": "string", "description": "e.g. USD"},
                    "to": {"type": "string", "description": "e.g. CNY"},
                },
                "required": ["amount", "from", "to"],
            },
        }
