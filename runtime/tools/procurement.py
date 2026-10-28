"""Synthetic procurement tools. Prices are derived from a hash, so they are stable across runs."""
from __future__ import annotations

import hashlib
from typing import Any

from runtime.tools.base import ToolDef

SUPPLIERS = [
    {"id": "SUP-ARGON", "name": "Argon Supply Co.", "categories": ["laptops", "monitors", "keyboards"], "rating": 4.6},
    {"id": "SUP-BASALT", "name": "Basalt Trading", "categories": ["laptops", "office chairs", "desks"], "rating": 4.1},
    {"id": "SUP-CIRRUS", "name": "Cirrus Tech", "categories": ["laptops", "monitors", "cloud credits"], "rating": 4.8},
    {"id": "SUP-DUNE", "name": "Dune Furnishings", "categories": ["office chairs", "desks"], "rating": 3.9},
    {"id": "SUP-EMBER", "name": "Ember Electronics", "categories": ["monitors", "keyboards", "headsets"], "rating": 4.3},
]
BASE_PRICE = {"laptops": 4200.0, "monitors": 1100.0, "keyboards": 180.0, "office chairs": 890.0, "desks": 1300.0,
              "cloud credits": 1.0, "headsets": 350.0}


def _factor(*parts: str) -> float:
    h = int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)
    return 0.82 + (h % 3600) / 10000  # 0.82 … 1.18


def search_suppliers(item: str) -> dict[str, Any]:
    hits = [s for s in SUPPLIERS if item.lower() in s["categories"]]
    return {"item": item, "suppliers": sorted(hits, key=lambda s: -s["rating"])}


def get_quote(supplier_id: str, item: str, quantity: int) -> dict[str, Any]:
    supplier = next((s for s in SUPPLIERS if s["id"] == supplier_id), None)
    if supplier is None or item not in supplier["categories"]:
        return {"error": "not_offered", "supplier_id": supplier_id, "item": item}
    unit = round(BASE_PRICE[item] * _factor(supplier_id, item) * (0.95 if quantity >= 20 else 1.0), 2)
    return {"supplier_id": supplier_id, "item": item, "quantity": quantity, "unit_price": unit,
            "total": round(unit * quantity, 2), "valid_days": 15}


def create_purchase_order(supplier_id: str, item: str, quantity: int, unit_price: float) -> dict[str, Any]:
    po = "PO-" + hashlib.sha256(f"{supplier_id}|{item}|{quantity}|{unit_price}".encode()).hexdigest()[:8].upper()
    return {"status": "created", "po_number": po, "supplier_id": supplier_id, "item": item, "quantity": quantity,
            "unit_price": unit_price, "total": round(unit_price * quantity, 2)}


TOOLS = [
    ToolDef("search_suppliers", "Suppliers that sell an item, best rated first.",
            {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"],
             "additionalProperties": False}, search_suppliers),
    ToolDef("get_quote", "A price quote from one supplier.",
            {"type": "object", "properties": {"supplier_id": {"type": "string"}, "item": {"type": "string"},
                                              "quantity": {"type": "integer", "minimum": 1}},
             "required": ["supplier_id", "item", "quantity"], "additionalProperties": False}, get_quote),
    ToolDef("create_purchase_order", "Creates a purchase order. Requires human approval.",
            {"type": "object", "properties": {"supplier_id": {"type": "string"}, "item": {"type": "string"},
                                              "quantity": {"type": "integer", "minimum": 1},
                                              "unit_price": {"type": "number", "exclusiveMinimum": 0}},
             "required": ["supplier_id", "item", "quantity", "unit_price"], "additionalProperties": False},
            create_purchase_order, writes=True),
]
