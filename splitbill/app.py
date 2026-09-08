"""
Split the Bill — Streamlit app.

Run:
    export GEMINI_API_KEY=...      # get one free at https://aistudio.google.com/apikey
    streamlit run app.py

Flow (mirrors the 3-stage pipeline in the models/extraction/calculator files):
  1. Upload    -> extract_bill() calls Gemini vision, returns a Bill
  2. Review    -> human edits any low-confidence field; nothing is trusted
                  blindly, especially the printed total
  3. Assign    -> who ate what (checkboxes per item per person)
  4. Split     -> compute_split() shows the final, proportionally-fair numbers
"""

import streamlit as st
import tempfile

from models import (
    Bill, BillSession, Person, ItemAssignment, LineItem, Charge,
    ChargeType, ConfidenceField, TextConfidenceField,
)
from extraction import extract_bill
from calculator import compute_split
import storage

st.set_page_config(page_title="Split the Bill", page_icon="🧾", layout="wide")

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
PRIMARY = "#2563eb"
PRIMARY_DARK = "#1d4ed8"
NAVY = "#0f172a"
BG = "#f8fafc"
CARD_BORDER = "#e2e8f0"

st.markdown(f"""
<style>
    .stApp {{ background-color: {BG}; }}

    section[data-testid="stSidebar"] {{
        background-color: {NAVY};
    }}
    section[data-testid="stSidebar"] * {{ color: #e2e8f0 !important; }}
    section[data-testid="stSidebar"] .sb-logo {{
        font-size: 1.4rem; font-weight: 800; color: white !important;
        margin-bottom: 0.1rem;
    }}
    section[data-testid="stSidebar"] .sb-tagline {{
        font-size: 0.8rem; color: #94a3b8 !important; margin-bottom: 1.5rem;
    }}

    div.stButton > button[kind="primary"] {{
        background-color: {PRIMARY}; border: none;
    }}
    div.stButton > button[kind="primary"]:hover {{
        background-color: {PRIMARY_DARK};
    }}

    .step-track {{ display: flex; gap: 0.5rem; margin-bottom: 1.5rem; }}
    .step {{
        flex: 1; padding: 0.75rem 1rem; border-radius: 10px;
        background: white; border: 1px solid {CARD_BORDER};
        display: flex; align-items: center; gap: 0.6rem;
    }}
    .step.active {{ border: 1.5px solid {PRIMARY}; background: #eff6ff; }}
    .step.done {{ opacity: 0.6; }}
    .step-num {{
        width: 26px; height: 26px; border-radius: 50%;
        background: #cbd5e1; color: white; font-weight: 700; font-size: 0.85rem;
        display: flex; align-items: center; justify-content: center; flex-shrink: 0;
    }}
    .step.active .step-num {{ background: {PRIMARY}; }}
    .step.done .step-num {{ background: #22c55e; }}
    .step-label {{ font-weight: 600; font-size: 0.85rem; color: {NAVY}; line-height: 1.1; }}
    .step-sub {{ font-size: 0.72rem; color: #64748b; }}

    .tip-box {{
        background: #f5f3ff; border: 1px solid #ddd6fe; border-radius: 10px;
        padding: 0.9rem 1.1rem; font-size: 0.85rem; color: #4c1d95;
    }}

    /* Hide Streamlit's auto-generated heading anchor-link icons for a cleaner look */
    [data-testid="stHeaderActionElements"] {{ display: none !important; }}
    h1 a, h2 a, h3 a, h4 a {{ display: none !important; }}

    /* Hide the top-right Deploy button + toolbar */
    [data-testid="stToolbar"] {{ display: none !important; }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state scaffolding
# ---------------------------------------------------------------------------
if "view" not in st.session_state:
    st.session_state.view = "app"          # "app" (the upload->...->result flow) or "history"
if "stage" not in st.session_state:
    st.session_state.stage = "upload"      # upload -> review -> people -> assign -> result
if "saved_record_id" not in st.session_state:
    st.session_state.saved_record_id = None
if "bill" not in st.session_state:
    st.session_state.bill: Bill | None = None
if "people" not in st.session_state:
    st.session_state.people: list[Person] = []
if "assignments" not in st.session_state:
    st.session_state.assignments: dict[str, dict[str, float]] = {}  # item_id -> {person_id: weight}


def confidence_badge(c: float) -> str:
    if c >= 0.85:
        return "🟢"
    if c >= 0.6:
        return "🟡"
    return "🔴"


# ---------------------------------------------------------------------------
# Sidebar branding
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="sb-logo">🧾 Split the Bill</div>'
        '<div class="sb-tagline">Smart Split. Fair Share.</div>',
        unsafe_allow_html=True,
    )
    st.markdown("---")

    if st.button("🏠 New Bill", use_container_width=True,
                 type="primary" if st.session_state.view == "app" else "secondary"):
        for k in ["stage", "bill", "people", "assignments", "saved_record_id"]:
            st.session_state.pop(k, None)
        st.session_state.view = "app"
        st.rerun()
    if st.button("🕘 History", use_container_width=True,
                 type="primary" if st.session_state.view == "history" else "secondary"):
        st.session_state.view = "history"
        st.rerun()

    st.markdown("---")
    st.caption("Runs locally · your bill photos go only to Gemini for reading, nowhere else.")

# ===========================================================================
# HISTORY VIEW
# ===========================================================================
if st.session_state.view == "history":
    st.title("🕘 History")
    records = storage.list_records()
    if not records:
        st.info("No saved splits yet. Finish a bill and hit **Save to history** on the results screen.")
    for r in records:
        title = r["restaurant_name"] or f"Bill on {r['bill_date'] or r['created_at'][:10]}"
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1.2, 0.6])
            c1.markdown(f"**{title}**")
            c1.caption(f"{r['created_at'][:16].replace('T', ' ')} · {r['num_items']} items · {r['num_people']} people")
            c2.markdown(f"### ₹{r['grand_total']:.2f}")
            if c3.button("🗑", key=f"delhist_{r['id']}"):
                storage.delete_record(r["id"])
                st.rerun()
            with st.expander("View split"):
                for p in r["people"]:
                    st.write(f"**{p['name']}: ₹{p['total_owed']:.2f}**")
                    for name, amt in p["item_lines"]:
                        st.caption(f"　- {name}: ₹{amt:.2f}")
                    st.caption(f"　- Tax + service charge share: ₹{p['charge_share']:.2f}")
    st.stop()

# ===========================================================================
# MAIN FLOW (view == "app")
# ===========================================================================

# ---------------------------------------------------------------------------
# Header + step tracker
# ---------------------------------------------------------------------------
st.title("🧾 Split the Bill")
st.caption("Upload your restaurant bill, assign items to people, and get the fair split — with taxes and service charge split proportionally, not by headcount.")

STEPS = [
    ("upload", "1", "Upload Bill", "Add a photo of your bill"),
    ("review", "2", "Review & Edit", "Verify the extracted details"),
    ("assign", "3", "Assign Items", "Decide who ate what"),
    ("result", "4", "View Results", "See the final split"),
]
# "people" stage visually counts as part of step 3 (assigning who's at the table)
stage_order = {"upload": 0, "review": 1, "people": 2, "assign": 2, "result": 3}
current_idx = stage_order.get(st.session_state.stage, 0)

cols = st.columns(4)
for i, (key, num, label, sub) in enumerate(STEPS):
    cls = "step"
    if i < current_idx:
        cls += " done"
    elif i == current_idx:
        cls += " active"
    cols[i].markdown(
        f'<div class="{cls}"><div class="step-num">{"✓" if i < current_idx else num}</div>'
        f'<div><div class="step-label">{label}</div><div class="step-sub">{sub}</div></div></div>',
        unsafe_allow_html=True,
    )

st.write("")

# ===========================================================================
# STAGE 1: Upload
# ===========================================================================
if st.session_state.stage == "upload":
    with st.container(border=True):
        st.markdown("#### 📤 Upload Bill Photo")
        st.caption("One photo normally; upload two if it's a long receipt split across two shots.")
        files = st.file_uploader("Bill photo(s)", type=["jpg", "jpeg", "png", "webp"],
                                  accept_multiple_files=True, label_visibility="collapsed")

        if files and st.button("Extract bill", type="primary"):
            paths = []
            for f in files:
                p = f"{tempfile.gettempdir()}/{f.name}"
                with open(p, "wb") as out:
                    out.write(f.getbuffer())
                paths.append(p)
            with st.spinner("Reading the bill..."):
                try:
                    bill = extract_bill(paths)
                    st.session_state.bill = bill
                    st.session_state.stage = "review"
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

    with st.expander("Or skip extraction and enter a bill manually"):
        if st.button("Start blank bill"):
            st.session_state.bill = Bill()
            st.session_state.stage = "review"
            st.rerun()

# ===========================================================================
# STAGE 2: Review — human fixes what the model misread, before any arithmetic
# ===========================================================================
elif st.session_state.stage == "review":
    bill = st.session_state.bill

    with st.container(border=True):
        st.markdown("#### 🧾 Review extracted items")
        st.caption("🔴 / 🟡 = low confidence, check these carefully. Nothing is calculated yet — fix numbers here first.")

        for idx, item in enumerate(bill.items):
            cols = st.columns([3, 1, 1, 1, 0.4])
            item.name.value = cols[0].text_input(
                f"Name {confidence_badge(item.name.confidence)}", item.name.value, key=f"name_{idx}")
            item.quantity.value = cols[1].number_input(
                f"Qty {confidence_badge(item.quantity.confidence)}", value=float(item.quantity.value), key=f"qty_{idx}")
            item.unit_price.value = cols[2].number_input(
                f"Price {confidence_badge(item.unit_price.confidence)}", value=float(item.unit_price.value), key=f"price_{idx}")
            item.line_total.value = cols[3].number_input(
                f"Line total {confidence_badge(item.line_total.confidence)}", value=float(item.line_total.value), key=f"lt_{idx}")
            if cols[4].button("🗑", key=f"del_{idx}"):
                bill.items.pop(idx)
                st.rerun()
            if item.has_internal_mismatch():
                st.warning(f"'{item.name.value}': qty×price = ₹{item.recomputed_total():.2f}, but printed line total is ₹{item.line_total.value:.2f}. Check which is right.")

        if st.button("+ Add item"):
            bill.items.append(LineItem(
                name=TextConfidenceField(value="", confidence=1.0),
                quantity=ConfidenceField(value=1, confidence=1.0),
                unit_price=ConfidenceField(value=0, confidence=1.0),
                line_total=ConfidenceField(value=0, confidence=1.0),
            ))
            st.rerun()

    with st.container(border=True):
        st.markdown("#### 💰 Taxes, service charge, discounts")
        for idx, c in enumerate(bill.charges):
            cols = st.columns([2, 1.2, 1, 1])
            c.label.value = cols[0].text_input(f"Label {confidence_badge(c.label.confidence)}", c.label.value, key=f"clabel_{idx}")
            c.charge_type = ChargeType(cols[1].selectbox(
                "Type", [t.value for t in ChargeType], index=[t.value for t in ChargeType].index(c.charge_type.value), key=f"ctype_{idx}"))
            if c.is_percentage_of_subtotal:
                c.percentage = cols[2].number_input("Percent", value=float(c.percentage or 0), key=f"cpct_{idx}")
            else:
                c.amount.value = cols[2].number_input(f"Amount {confidence_badge(c.amount.confidence)}", value=float(c.amount.value), key=f"camt_{idx}")
            c.is_percentage_of_subtotal = cols[3].checkbox("% of subtotal", value=c.is_percentage_of_subtotal, key=f"cpctflag_{idx}")

        if st.button("+ Add charge"):
            bill.charges.append(Charge(
                label=TextConfidenceField(value="", confidence=1.0),
                charge_type=ChargeType.OTHER,
                amount=ConfidenceField(value=0, confidence=1.0),
            ))
            st.rerun()

    with st.container(border=True):
        st.markdown("#### 🧮 Printed totals (as shown on the bill)")
        c1, c2 = st.columns(2)
        subtotal_val = c1.number_input("Printed subtotal", value=float(bill.printed_subtotal.value) if bill.printed_subtotal else 0.0)
        total_val = c2.number_input("Printed total", value=float(bill.printed_total.value) if bill.printed_total else 0.0)
        bill.printed_subtotal = ConfidenceField(value=subtotal_val, confidence=1.0)
        bill.printed_total = ConfidenceField(value=total_val, confidence=1.0)

        st.info(f"Recomputed from items + charges: subtotal ₹{bill.computed_subtotal():.2f}, total ₹{bill.computed_total():.2f}")
        mismatch = bill.total_mismatch()
        if mismatch is not None:
            st.warning(f"⚠️ Printed total differs from recomputed total by ₹{mismatch:+.2f}. This bill may genuinely have a printing error — you'll choose which number to split against on the next screen.")

    if st.button("Looks good — assign people", type="primary"):
        st.session_state.stage = "people"
        st.rerun()

# ===========================================================================
# STAGE 3: People
# ===========================================================================
elif st.session_state.stage == "people":
    with st.container(border=True):
        st.markdown("#### 👥 Who was at the table?")
        names_str = st.text_area("One name per line", value="\n".join(p.name for p in st.session_state.people) or "")
        if st.button("Set people", type="primary"):
            names = [n.strip() for n in names_str.splitlines() if n.strip()]
            st.session_state.people = [Person(name=n) for n in names]
            st.session_state.stage = "assign"
            st.rerun()
        if st.button("← Back to review"):
            st.session_state.stage = "review"
            st.rerun()

# ===========================================================================
# STAGE 4: Assign — who ate what
# ===========================================================================
elif st.session_state.stage == "assign":
    bill = st.session_state.bill
    people = st.session_state.people

    with st.container(border=True):
        st.markdown("#### 🍽️ Who ate what?")
        st.caption("Tick everyone who shared an item. Shared items split evenly by default — use the weight box for uneven splits.")

        for item in bill.items:
            st.markdown(f"**{item.name.value}** — ₹{item.recomputed_total():.2f}")
            cols = st.columns(len(people) + 1)
            current = st.session_state.assignments.get(item.id, {})
            for i, p in enumerate(people):
                checked = p.id in current
                new_checked = cols[i].checkbox(p.name, value=checked, key=f"chk_{item.id}_{p.id}")
                if new_checked:
                    weight = cols[i].number_input("wt", value=float(current.get(p.id, 1.0)), key=f"wt_{item.id}_{p.id}", label_visibility="collapsed", min_value=0.01)
                    current[p.id] = weight
                elif p.id in current:
                    del current[p.id]
            if cols[-1].button("All", key=f"all_{item.id}"):
                current = {p.id: 1.0 for p in people}
            st.session_state.assignments[item.id] = current
            st.divider()

        unassigned = [i for i in bill.items if not st.session_state.assignments.get(i.id)]
        if unassigned:
            st.error(f"Not yet assigned: {', '.join(i.name.value for i in unassigned)}")
        else:
            if st.button("Split the bill", type="primary"):
                st.session_state.stage = "result"
                st.rerun()

    if st.button("← Back"):
        st.session_state.stage = "people"
        st.rerun()

# ===========================================================================
# STAGE 5: Result
# ===========================================================================
elif st.session_state.stage == "result":
    bill = st.session_state.bill
    assignments = [ItemAssignment(item_id=iid, person_weights=w) for iid, w in st.session_state.assignments.items() if w]
    session = BillSession(bill=bill, people=st.session_state.people, assignments=assignments)

    reconcile = True
    if bill.total_mismatch() is not None:
        reconcile = st.radio(
            "The printed total looked wrong. Split against:",
            ["Printed total (pay what the bill says)", "Recomputed total (pay the mathematically correct amount)"],
        ) == "Printed total (pay what the bill says)"

    try:
        result = compute_split(session, reconcile_to_printed=reconcile)
    except ValueError as e:
        st.error(str(e))
        st.stop()

    if result.mismatch_flag:
        st.warning(result.mismatch_flag)

    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"### Total: ₹{result.grand_total:.2f}")
            st.caption(f"Subtotal ₹{result.subtotal:.2f} + taxes/service charge ₹{result.total_charges:.2f}")
        with c2:
            if st.session_state.saved_record_id:
                st.success("Saved ✓")
            elif st.button("💾 Save to history", type="primary"):
                st.session_state.saved_record_id = storage.save_record(bill, st.session_state.people, result)
                st.rerun()

    for pb in result.people:
        with st.expander(f"**{pb.person_name}: ₹{pb.total_owed:.2f}**", expanded=True):
            for name, amt in pb.item_lines:
                st.write(f"- {name}: ₹{amt:.2f}")
            st.write(f"- Tax + service charge share: ₹{pb.charge_share:.2f}")

    if st.button("← Start over"):
        for k in ["stage", "bill", "people", "assignments", "saved_record_id"]:
            st.session_state.pop(k, None)
        st.rerun()