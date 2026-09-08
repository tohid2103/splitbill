"""
Core data models for Split the Bill.

Design notes:
- Every field extracted from a photo carries a confidence score (0-1) so the
  review screen can flag what the model is unsure about. Confidence is
  attached via `ConfidenceField`, a small wrapper, rather than a parallel
  dict, so it can never drift out of sync with the value.
- Nothing downstream of extraction (assignment, calculation) touches a raw
  photo again. Once a human has reviewed and confirmed the `Bill`, it is
  treated as ground truth for arithmetic purposes.
- Money is stored as float (rupees, 2dp). For a real production system you'd
  want Decimal/paisa-as-int to avoid float rounding drift, but float is fine
  for a personal tool and keeps JSON round-tripping simple. `calculator.py`
  rounds at the very end only.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Confidence wrapper
# ---------------------------------------------------------------------------

class ConfidenceField(BaseModel):
    """A value plus how sure the extraction model was about it."""

    value: float
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def needs_review(self) -> bool:
        # Threshold is deliberately generous — better to over-flag than
        # silently trust a misread price.
        return self.confidence < 0.85


class TextConfidenceField(BaseModel):
    """Same idea as ConfidenceField but for strings (item names, etc.)."""

    value: str
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def needs_review(self) -> bool:
        return self.confidence < 0.85


# ---------------------------------------------------------------------------
# Bill contents
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    name: TextConfidenceField
    quantity: ConfidenceField  # stored as float to allow e.g. 0.5 kg items
    unit_price: ConfidenceField
    line_total: ConfidenceField  # as printed on the bill (qty * unit_price
    # printed by the restaurant — can legitimately disagree with our own
    # qty*price recompute if the bill itself has an error; we keep both
    # so a mismatch is visible instead of silently "corrected")

    def recomputed_total(self) -> float:
        return round(self.quantity.value * self.unit_price.value, 2)

    def has_internal_mismatch(self, tolerance: float = 0.5) -> bool:
        return abs(self.recomputed_total() - self.line_total.value) > tolerance


class ChargeType(str, Enum):
    TAX = "tax"                    # e.g. GST, CGST, SGST
    SERVICE_CHARGE = "service_charge"
    DISCOUNT = "discount"
    OTHER = "other"


class Charge(BaseModel):
    """A non-item line: GST, service charge, discount, packing charge, etc."""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    label: TextConfidenceField     # e.g. "CGST 2.5%", "Service Charge"
    charge_type: ChargeType
    amount: ConfidenceField        # signed: discounts should be negative
    is_percentage_of_subtotal: bool = False
    percentage: Optional[float] = None  # if the bill states "10%" explicitly


class ExtractionMeta(BaseModel):
    """Bookkeeping about how this bill was read."""

    source_images: list[str] = Field(default_factory=list)  # file paths/ids
    overall_confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    reviewed_by_human: bool = False
    notes: Optional[str] = None


class Bill(BaseModel):
    restaurant_name: Optional[TextConfidenceField] = None
    date: Optional[TextConfidenceField] = None
    items: list[LineItem] = Field(default_factory=list)
    charges: list[Charge] = Field(default_factory=list)

    printed_subtotal: Optional[ConfidenceField] = None
    printed_total: Optional[ConfidenceField] = None

    meta: ExtractionMeta = Field(default_factory=ExtractionMeta)

    # ---- derived, bill-only math (no people involved yet) ----

    def computed_subtotal(self) -> float:
        return round(sum(item.recomputed_total() for item in self.items), 2)

    def computed_total(self) -> float:
        total = self.computed_subtotal()
        for c in self.charges:
            amt = c.amount.value
            if c.is_percentage_of_subtotal and c.percentage is not None:
                amt = round(self.computed_subtotal() * c.percentage / 100, 2)
            total += amt  # discounts are stored negative
        return round(total, 2)

    def total_mismatch(self, tolerance: float = 1.0) -> Optional[float]:
        """Returns the delta if our computed total disagrees with the
        printed total by more than `tolerance`, else None.
        This is exactly the check that catches "the printed total is
        genuinely wrong" bills — we never silently trust the printed
        number, and we never silently trust our own re-sum either; we
        surface the disagreement for a human to resolve."""
        if self.printed_total is None:
            return None
        delta = round(self.computed_total() - self.printed_total.value, 2)
        return delta if abs(delta) > tolerance else None


# ---------------------------------------------------------------------------
# People & assignment
# ---------------------------------------------------------------------------

class Person(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    name: str
    left_early: bool = False  # informational only; doesn't change math,
    # the assignment step is what actually determines what they owe


class ItemAssignment(BaseModel):
    """Who ate a given item, and in what proportion.

    weights need not sum to 1 — they're normalized at calculation time.
    Equal split among N people: give each person weight 1.
    Unequal split (e.g. one person had a bigger share): give real weights.
    "Everyone" items (e.g. shared starters, water) should list every
    Person id with equal weight.
    """

    item_id: str
    person_weights: dict[str, float]  # person_id -> weight, must be non-empty

    @model_validator(mode="after")
    def _non_empty(self):
        if not self.person_weights:
            raise ValueError(
                f"Item {self.item_id} has no assigned people — "
                "every item must be assigned to at least one person, "
                "or explicitly marked as an unclaimed/void item."
            )
        return self


class BillSession(BaseModel):
    """Everything needed to go from a reviewed Bill to a per-person split."""

    bill: Bill
    people: list[Person]
    assignments: list[ItemAssignment]

    def unassigned_items(self) -> list[str]:
        assigned_ids = {a.item_id for a in self.assignments}
        return [i.id for i in self.bill.items if i.id not in assigned_ids]
