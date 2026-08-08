"""
rcm_rules.py
------------
Deterministic post-processing pass applied AFTER the AI extracts raw values
from the document. This encodes the standard RCM payment-posting formulas
so that arithmetic — which LLMs can get subtly wrong — is handled reliably
in code instead.

Formulas (standard US medical billing / RCM conventions):
    BA = AA + CA        (Billed = Allowed + Contractual Adjustment)
    AA = PA + PR         (Allowed = Paid + Patient Responsibility)
    PR = PR1 + PR2 + PR3 (Patient Resp = Deductible + Coinsurance + Copay)

Claim status logic:
    If PA is present (nonzero) OR PR is present (nonzero) -> PAID
    If BOTH are zero/blank -> DENIED
    If neither PA nor PR was extracted at all -> unknown (null)

Posting-rule awareness:
    Secondary/Tertiary claims legitimately have no AA/CA — this is expected,
    not an extraction gap, so rows are only flagged for review when values
    that ARE present fail to reconcile with each other.
"""

TOLERANCE = 0.02  # allow for rounding cents


def _to_float(value):
    """Best-effort parse of a value into a float, or None if not parseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        if cleaned in ("", "-", "N/A", "null", "None"):
            return None
        # Handle values written like "(35.00)" meaning negative/write-off
        negative = cleaned.startswith("(") and cleaned.endswith(")")
        if negative:
            cleaned = cleaned[1:-1]
        try:
            num = float(cleaned)
            return -num if negative else num
        except ValueError:
            return None
    return None


def _fmt(value):
    """Format a float back to a 2-decimal string, matching the row's style."""
    if value is None:
        return None
    return f"{value:.2f}"


def _is_secondary_or_tertiary(row):
    level = (row.get("payer_level") or "").strip().lower()
    return level in ("secondary", "tertiary")


def _fill_amount_formulas(row):
    """
    Fills in any ONE missing value among BA/AA/CA and AA/PA/PR when the
    other two are known, using the standard formulas. Never overwrites a
    value the AI actually extracted.

    Per RCM posting rules, Secondary/Tertiary claims should NOT have
    AA/CA populated even if derivable — only BA, PA, and PR are posted
    for those payer levels — so this skips deriving AA/CA in that case.
    """
    secondary_or_tertiary = _is_secondary_or_tertiary(row)

    ba = _to_float(row.get("billed_amount"))
    aa = _to_float(row.get("allowed_amount"))
    ca = _to_float(row.get("contractual_adjustment"))
    pa = _to_float(row.get("paid_amount"))
    pr1 = _to_float(row.get("deductible"))
    pr2 = _to_float(row.get("coinsurance"))
    pr3 = _to_float(row.get("copay"))
    pr_total = _to_float(row.get("patient_responsibility"))

    # BA = AA + CA  (skip deriving AA/CA themselves for secondary/tertiary)
    if not secondary_or_tertiary:
        if ba is None and aa is not None and ca is not None:
            ba = aa + ca
            row["billed_amount"] = _fmt(ba)
        elif ca is None and ba is not None and aa is not None:
            ca = ba - aa
            row["contractual_adjustment"] = _fmt(ca)
        elif aa is None and ba is not None and ca is not None:
            aa = ba - ca
            row["allowed_amount"] = _fmt(aa)

    # PR total = PR1 + PR2 + PR3, only if all three components are present
    if pr_total is None and pr1 is not None and pr2 is not None and pr3 is not None:
        pr_total = pr1 + pr2 + pr3
        row["patient_responsibility"] = _fmt(pr_total)

    # AA = PA + PR (skip deriving AA for secondary/tertiary — not posted there)
    if not secondary_or_tertiary:
        if aa is None and pa is not None and pr_total is not None:
            aa = pa + pr_total
            row["allowed_amount"] = _fmt(aa)
        elif pr_total is None and aa is not None and pa is not None:
            pr_total = aa - pa
            row["patient_responsibility"] = _fmt(pr_total)

    # PA = AA - PR — this one is safe to derive regardless of payer level,
    # since PA is always posted (primary, secondary, or tertiary).
    if pa is None and aa is not None and pr_total is not None:
        pa = aa - pr_total
        row["paid_amount"] = _fmt(pa)

    return row


def _validate_reconciliation(row):
    """
    Cross-checks BA = AA + CA and AA = PA + PR using whatever values are
    present (extracted or filled in above). Adds needs_review/review_notes
    if the numbers don't add up within rounding tolerance. Does NOT flag
    rows simply for having nulls (e.g. secondary claims missing AA/CA).
    """
    notes = []

    ba = _to_float(row.get("billed_amount"))
    aa = _to_float(row.get("allowed_amount"))
    ca = _to_float(row.get("contractual_adjustment"))
    pa = _to_float(row.get("paid_amount"))
    pr_total = _to_float(row.get("patient_responsibility"))

    if ba is not None and aa is not None and ca is not None:
        if abs(ba - (aa + ca)) > TOLERANCE:
            notes.append(
                f"BA ({ba:.2f}) does not equal AA + CA ({aa:.2f} + {ca:.2f} = {aa + ca:.2f})"
            )

    if aa is not None and pa is not None and pr_total is not None:
        if abs(aa - (pa + pr_total)) > TOLERANCE:
            notes.append(
                f"AA ({aa:.2f}) does not equal PA + PR ({pa:.2f} + {pr_total:.2f} = {pa + pr_total:.2f})"
            )

    row["needs_review"] = bool(notes)
    row["review_notes"] = notes
    return row


def _compute_claim_status(row):
    """
    Applies the standard EOB status logic:
    PA present (nonzero) OR PR present (nonzero) -> PAID
    Both zero/blank -> DENIED
    Neither field extracted at all -> status left null (insufficient info)
    """
    pa = _to_float(row.get("paid_amount"))
    pr = _to_float(row.get("patient_responsibility"))

    if pa is None and pr is None:
        row["claim_status"] = None
        return row

    pa_present = bool(pa) and pa != 0
    pr_present = bool(pr) and pr != 0

    row["claim_status"] = "PAID" if (pa_present or pr_present) else "DENIED"
    return row


def apply_rcm_rules(rows: list[dict]) -> list[dict]:
    """
    Runs the full deterministic post-processing pass over every extracted
    row: fills solvable missing amounts, validates reconciliation, and
    computes claim status. Returns the same rows, enriched in place.
    """
    processed = []
    for row in rows:
        row = _fill_amount_formulas(row)
        row = _validate_reconciliation(row)
        row = _compute_claim_status(row)
        processed.append(row)
    return processed
