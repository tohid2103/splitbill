"""
Simple local persistence for saved bill splits.

Design: one JSON file per saved split under data/bills/. No database — this
is a personal tool run by one person on one machine, so a folder of JSON
files is transparent (you can open and read any record directly), needs no
setup, and is trivial to back up or wipe (just delete the data/ folder).

Only a flattened summary of the result is stored (not the raw Bill/
BillSession Pydantic objects) — this keeps the saved format simple and
stable even if the internal models change shape later.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "bills"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def save_record(bill, people, result) -> str:
    """Persist a completed split. Returns the record id."""
    record_id = uuid.uuid4().hex[:10]
    record = {
        "id": record_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "restaurant_name": bill.restaurant_name.value if bill.restaurant_name else None,
        "bill_date": bill.date.value if bill.date else None,
        "num_items": len(bill.items),
        "num_people": len(people),
        "subtotal": result.subtotal,
        "total_charges": result.total_charges,
        "grand_total": result.grand_total,
        "people": [
            {
                "name": pb.person_name,
                "total_owed": pb.total_owed,
                "charge_share": pb.charge_share,
                "item_lines": pb.item_lines,
            }
            for pb in result.people
        ],
    }
    (DATA_DIR / f"{record_id}.json").write_text(json.dumps(record, indent=2))
    return record_id


def list_records() -> list[dict]:
    """All saved records, most recent first."""
    records = []
    for f in DATA_DIR.glob("*.json"):
        try:
            records.append(json.loads(f.read_text()))
        except Exception:
            continue  # skip anything corrupted rather than crashing history view
    return sorted(records, key=lambda r: r["created_at"], reverse=True)


def delete_record(record_id: str) -> None:
    f = DATA_DIR / f"{record_id}.json"
    if f.exists():
        f.unlink()