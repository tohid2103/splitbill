# 🧾 Split the Bill

Photo in, fair split out.

Upload a photo of a restaurant bill, say who ate what, and get back exactly
what each person owes — with tax and service charge distributed by what
they actually ate, **not** divided evenly by headcount.

---

## What it does

- 📤 **Photo → structured bill.** Upload one or two photos (long thermal
  receipts often need two); Gemini vision reads out every item, quantity,
  price, tax line, service charge, and discount.
- ✅ **Human review before any math runs.** Every extracted field carries a
  confidence score. Low-confidence fields are flagged 🔴/🟡 so you can fix
  what the model misread — nothing is calculated until you confirm.
- 🍽️ **Fair assignment.** Every item goes to one person, several people
  (shared, split proportionally), or everyone.
- 💰 **Proportional tax/service charge.** Distributed by each person's share
  of the subtotal — someone who only had a Coke doesn't pay 1/7th of the
  service charge on everyone else's biryani.
- ⚠️ **Catches bad bills.** If the printed total doesn't match what the line
  items + charges actually add up to, the app flags it and lets you choose
  which number to trust — instead of silently believing the receipt.
- 🕘 **History.** Save any completed split and browse past ones later.

---

## Quick start (Windows)

```
cd splitbill
pip install -r requirements.txt
$env:GEMINI_API_KEY="your-key-here"
streamlit run app.py
```

macOS/Linux: same, but use `export GEMINI_API_KEY=your-key-here` instead of
the `$env:` line.

The app opens automatically at `http://localhost:8501`. If it doesn't,
open that link yourself in a browser.

### Getting a free Gemini API key

1. Go to **https://aistudio.google.com/apikey** and sign in with a Google
   account.
2. Click **Create API key** — no credit card required.
3. Google AI Studio's free tier includes vision (photo understanding) at no
   cost, with rate limits generous enough for personal use. If you ever hit
   a limit, it resets on its own — you're never charged.
4. Paste the key into the `$env:GEMINI_API_KEY=...` command above, in the
   **same terminal window** you'll run `streamlit run app.py` from. This
   only lasts for that terminal session — set it again each time you open
   a new terminal.

---

## Project structure

```
splitbill/
├── app.py              — Streamlit UI: the whole user-facing flow
├── models.py            — Pydantic schema (Bill, LineItem, Charge, Person, ...)
├── extraction.py         — Photo(s) → Gemini vision → validated Bill
├── calculator.py          — The actual split math
├── storage.py              — Saves/loads completed splits (History feature)
├── requirements.txt
├── data/bills/               — auto-created; one JSON file per saved split
└── tests/
    ├── test_accuracy.py       — scores extraction against your labeled bills
    ├── bill_photos/            — put your 12+ test photos here
    └── ground_truth/            — hand-labeled JSON, one per test bill
        └── _TEMPLATE.json
```

| File | What it does |
|---|---|
| `models.py` | Defines the data shapes: `Bill`, `LineItem`, `Charge`, `Person`, `ItemAssignment`. Every extracted field carries a confidence score. |
| `extraction.py` | Sends bill photo(s) to Gemini vision, turns the response into a validated `Bill`. Handles multi-photo bills. |
| `calculator.py` | Does the split math — proportional item sharing, and tax/service charge distributed by what each person ate. Flags printed-total mismatches. |
| `app.py` | The Streamlit app itself: Upload → Review → Assign → Result, plus History. |
| `storage.py` | Saves/lists/deletes completed splits as JSON files under `data/bills/`. |
| `tests/test_accuracy.py` | Runs extraction against your hand-labeled test bills and scores accuracy. |

---

## How to use it, step by step

1. **Upload** — drop in the bill photo(s).
2. **Review & Edit** — check what the model read. 🔴/🟡 marks mean "check this
   carefully" — fix anything wrong before moving on. Nothing is calculated yet.
3. **Assign Items** — say who ate what. Tick multiple people on a shared
   item and it splits evenly by default (or set custom weights for uneven
   shares).
4. **View Results** — everyone's exact total, broken down by item + their
   share of tax/service charge. Hit **💾 Save to history** if you want to
   keep it.

---

## History

Hit **💾 Save to history** on the results screen to keep a split. **🕘 History**
in the sidebar lists everything you've saved — restaurant/date, total, and
a per-person breakdown, most recent first.

Data lives in `data/bills/*.json` — one small, plain-text file per saved
bill. No database, nothing hidden; open any file in Notepad to see exactly
what's stored. Delete the `data` folder any time to wipe all history.

⚠️ **This folder lives inside `splitbill/`.** If you ever replace the whole
project folder (e.g. extracting a fresh zip), copy `data/` over first, or
your saved history goes with the old folder.

---

## Building your 12-bill test set

Put photos in `tests/bill_photos/`. For each one, copy
`tests/ground_truth/_TEMPLATE.json` to `tests/ground_truth/bill_XX.json` and
fill in what the bill *actually* says — by hand, carefully.

Make sure your 12+ bills cover:

- [ ] Dim light
- [ ] Crumpled paper
- [ ] Steep photo angle
- [ ] Faded thermal print
- [ ] Handwriting on the bill
- [ ] Two scripts / languages (e.g. Hindi + English)
- [ ] A bill long enough to need two photos (list both in `photo_files`)
- [ ] **One bill whose printed total is genuinely wrong** — set
      `"printed_total_is_wrong": true` in its ground truth. This checks
      whether the app trusts the printed number blindly (it shouldn't).
- [ ] A few normal, easy bills as a baseline

Then run:

```
python -m tests.test_accuracy
```

This reports item count match, per-field accuracy, whether low-confidence
flags actually correlated with wrong fields, and whether the
deliberately-wrong-total bill got caught.

---

## Why it's built this way

- **Confidence is per field, not per bill.** One illegible handwritten item
  shouldn't force you to re-check a bill that's otherwise perfectly clear.
- **Review happens before any math.** The split calculation only ever runs
  on data you've confirmed — never on raw, unreviewed model output.
- **The printed total is treated as a claim, not a fact.** If the receipt's
  own total doesn't match what its line items + charges add up to, the app
  asks *you* which number to trust instead of silently picking one.
- **Tax and service charge scale with what you ate**, not with headcount.
  Each person's share = their pre-tax spend × (total charges ÷ subtotal).
- **Rounding leftovers** get absorbed by whoever has the largest share, so
  the displayed amounts always add up exactly to the total.

---

## Troubleshooting

**`404 NOT_FOUND ... model is no longer available`**
Google retires model names sometimes. Open `extraction.py`, find the
`MODEL = "..."` line near the top, and swap in whatever model Google's
error message points you to.

**`'GEMINI_API_KEY=...' is not recognized...` (PowerShell)**
PowerShell needs `$env:GEMINI_API_KEY="..."` — not `set GEMINI_API_KEY=...`
(that's Command Prompt syntax) and not the key typed on its own.

**Key stops working after closing the terminal**
`$env:`/`export` only lasts for that terminal session. Set it again each
time you open a new terminal, before running `streamlit run app.py`.

**App crashes on photo upload**
Make sure you're on the latest `app.py` — older versions used a hardcoded
`/tmp/` path that doesn't exist on Windows.

---

## Known limitations

- Money is stored as `float` — fine for personal use, but not pedantically
  rounding-safe. Swap for `Decimal` if that matters to you.
- Very long two-photo bills rely on the model correctly avoiding duplicate
  items across the two photos — worth checking in your test set.
- History is local-only — JSON files on your computer, no cloud sync.