"""
The actual arithmetic everyone argues about.

Core rule: taxes, service charge, and discounts are distributed across
people in proportion to what they ate (their share of the pre-tax subtotal),
NEVER split evenly by headcount. Someone who only had a Coke should not pay
1/7th of the service charge on everyone else's biryani.

Algorithm:
1. For each item, split its (recomputed) line total across its assigned
   people according to their weights -> gives each person a "pre-tax spend".
2. Sum charges (tax, service charge — positive; discount — negative) into a
   single net-charge-rate = total_charges / subtotal.
3. Each person's charge share = their pre-tax spend * net-charge-rate.
   This is equivalent to distributing each individual charge line
   proportionally and then summing, but numerically simpler and avoids
   double-rounding per charge line.
4. Round only at the very end, and reconcile the last paisa/rupee of
   rounding drift onto whoever has the largest share, so per-person totals
   always sum exactly to the bill total (or to the printed total, if the
   caller chooses to reconcile against that instead of our recomputed one).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models import BillSession


@dataclass
class PersonBreakdown:
    person_id: str
    person_name: str
    item_lines: list[tuple[str, float]] = field(default_factory=list)  # (item name, amount)
    pretax_spend: float = 0.0
    charge_share: float = 0.0
    total_owed: float = 0.0


@dataclass
class SplitResult:
    people: list[PersonBreakdown]
    subtotal: float
    total_charges: float
    grand_total: float
    reconciled_against_printed: bool
    mismatch_flag: str | None  # human-readable warning, or None


def compute_split(session: BillSession, reconcile_to_printed: bool = True) -> SplitResult:
    bill = session.bill
    people_by_id = {p.id: p for p in session.people}

    unassigned = session.unassigned_items()
    if unassigned:
        names = [i.name.value for i in bill.items if i.id in unassigned]
        raise ValueError(
            f"Cannot compute split — these items have no one assigned: {names}. "
            "Every item must be assigned before arithmetic runs."
        )

    breakdown = {pid: PersonBreakdown(person_id=pid, person_name=p.name)
                 for pid, p in people_by_id.items()}

    assignment_by_item = {a.item_id: a for a in session.assignments}

    subtotal = 0.0
    for item in bill.items:
        item_total = item.recomputed_total()
        subtotal += item_total
        assignment = assignment_by_item[item.id]
        weight_sum = sum(assignment.person_weights.values())
        for pid, w in assignment.person_weights.items():
            share = round(item_total * (w / weight_sum), 4)
            breakdown[pid].pretax_spend += share
            breakdown[pid].item_lines.append((item.name.value, share))
    subtotal = round(subtotal, 2)

    total_charges = 0.0
    for c in bill.charges:
        amt = c.amount.value
        if c.is_percentage_of_subtotal and c.percentage is not None:
            amt = subtotal * c.percentage / 100
        total_charges += amt
    total_charges = round(total_charges, 2)

    charge_rate = (total_charges / subtotal) if subtotal else 0.0
    for pid, pb in breakdown.items():
        pb.charge_share = round(pb.pretax_spend * charge_rate, 2)
        pb.total_owed = round(pb.pretax_spend + pb.charge_share, 2)

    computed_total = round(subtotal + total_charges, 2)

    target_total = computed_total
    mismatch_flag = None
    mismatch = bill.total_mismatch()
    if mismatch is not None:
        mismatch_flag = (
            f"Printed total (₹{bill.printed_total.value:.2f}) differs from "
            f"recomputed total (₹{computed_total:.2f}) by ₹{mismatch:+.2f}. "
            f"{'Splitting against the recomputed (correct) total.' if not reconcile_to_printed else 'Splitting against the printed total as requested — verify before paying.'}"
        )
        if reconcile_to_printed and bill.printed_total is not None:
            target_total = bill.printed_total.value

    # Reconcile rounding drift (and any printed-total override) onto the
    # largest-share person so individual totals always sum exactly.
    people_sorted = sorted(breakdown.values(), key=lambda pb: -pb.total_owed)
    current_sum = round(sum(pb.total_owed for pb in people_sorted), 2)
    drift = round(target_total - current_sum, 2)
    if people_sorted and abs(drift) > 0:
        people_sorted[0].total_owed = round(people_sorted[0].total_owed + drift, 2)

    return SplitResult(
        people=people_sorted,
        subtotal=subtotal,
        total_charges=total_charges,
        grand_total=target_total,
        reconciled_against_printed=(reconcile_to_printed and mismatch is not None),
        mismatch_flag=mismatch_flag,
    )
