"""
Accuracy harness for the 12-bill test set.

Usage:
    1. Drop bill photos into tests/bill_photos/  (e.g. bill_01.jpg, bill_01b.jpg for a two-photo bill)
    2. For each, write ground truth into tests/ground_truth/bill_01.json — see
       tests/ground_truth/_TEMPLATE.json for the shape.
    3. Run:  python -m tests.test_accuracy

What it measures (this is the actual point of the 12-bill set):
    - item count match (did it miss/hallucinate items)
    - per-field accuracy: name (fuzzy), quantity, unit_price, line_total
    - whether it correctly flagged the deliberately-wrong-total bill
      (i.e. did total_mismatch() fire when it should)
    - overall confidence calibration: were low-confidence fields the ones
      that were actually wrong? (this tells you if confidence is useful
      or just noise)

This is NOT unit testing in the pytest-assertion sense — real photos are
noisy and the model will not be 100% every run. It's a scorecard you re-run
after prompt or model changes to see if accuracy is moving in the right
direction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from difflib import SequenceMatcher

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extraction import extract_bill  # noqa: E402

GROUND_TRUTH_DIR = Path(__file__).parent / "ground_truth"
PHOTOS_DIR = Path(__file__).parent / "bill_photos"


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def close_enough(pred: float, truth: float, abs_tol: float = 1.0, rel_tol: float = 0.02) -> bool:
    return abs(pred - truth) <= max(abs_tol, rel_tol * abs(truth))


def score_bill(gt: dict, photo_ids: list[str]) -> dict:
    paths = [str(PHOTOS_DIR / pid) for pid in photo_ids]
    bill = extract_bill(paths)

    report = {
        "bill_id": gt["bill_id"],
        "condition": gt.get("condition", ""),
        "item_count_truth": len(gt["items"]),
        "item_count_predicted": len(bill.items),
        "field_results": [],
        "total_mismatch_expected": gt.get("printed_total_is_wrong", False),
        "total_mismatch_detected": bill.total_mismatch() is not None,
    }

    # Greedy match predicted items to ground truth items by name similarity,
    # so a shuffled item order doesn't tank the score.
    remaining_pred = list(bill.items)
    for truth_item in gt["items"]:
        if not remaining_pred:
            report["field_results"].append({"item": truth_item["name"], "matched": False})
            continue
        best = max(remaining_pred, key=lambda p: name_similarity(p.name.value, truth_item["name"]))
        remaining_pred.remove(best)
        report["field_results"].append({
            "item": truth_item["name"],
            "matched": True,
            "name_similarity": round(name_similarity(best.name.value, truth_item["name"]), 2),
            "quantity_correct": close_enough(best.quantity.value, truth_item["quantity"]),
            "unit_price_correct": close_enough(best.unit_price.value, truth_item["unit_price"]),
            "line_total_correct": close_enough(best.line_total.value, truth_item["line_total"]),
            "was_flagged_low_confidence": best.name.needs_review or best.quantity.needs_review or best.unit_price.needs_review,
        })

    report["unmatched_extra_predictions"] = len(remaining_pred)
    return report


def main():
    truth_files = sorted(GROUND_TRUTH_DIR.glob("bill_*.json"))
    if not truth_files:
        print(f"No ground truth files found in {GROUND_TRUTH_DIR}. "
              f"Copy _TEMPLATE.json and fill one in per test bill.")
        return

    all_reports = []
    for tf in truth_files:
        gt = json.loads(tf.read_text())
        photo_ids = gt["photo_files"]
        print(f"\n=== {gt['bill_id']} ({gt.get('condition', 'no condition noted')}) ===")
        try:
            report = score_bill(gt, photo_ids)
        except Exception as e:
            print(f"  EXTRACTION FAILED: {e}")
            continue
        all_reports.append(report)

        correct_fields = sum(
            1 for f in report["field_results"] if f.get("matched")
            and f["quantity_correct"] and f["unit_price_correct"] and f["line_total_correct"]
        )
        total_fields = len(report["field_results"])
        print(f"  Items: {report['item_count_predicted']}/{report['item_count_truth']} predicted/truth, "
              f"extra unmatched: {report['unmatched_extra_predictions']}")
        print(f"  Fully-correct items: {correct_fields}/{total_fields}")
        if report["total_mismatch_expected"]:
            status = "✓ caught it" if report["total_mismatch_detected"] else "✗ MISSED the bad total"
            print(f"  Deliberately-wrong-total check: {status}")

    print("\n\n=== SUMMARY ===")
    total_items = sum(r["item_count_truth"] for r in all_reports)
    total_correct = sum(
        sum(1 for f in r["field_results"] if f.get("matched") and f["quantity_correct"]
            and f["unit_price_correct"] and f["line_total_correct"])
        for r in all_reports
    )
    print(f"Bills tested: {len(all_reports)}")
    print(f"Item-level accuracy: {total_correct}/{total_items} ({100*total_correct/total_items:.1f}%)" if total_items else "no items")


if __name__ == "__main__":
    main()
