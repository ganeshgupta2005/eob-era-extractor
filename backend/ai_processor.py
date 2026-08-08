"""
ai_processor.py
----------------
Sends extracted EOB/ERA/ERN text to a free AI model for structured
extraction, with automatic failover:

    1. Try Gemini (Google's free tier) first.
    2. If the Gemini API call itself errors or is unreachable, fall back
       to Groq (free tier) automatically — no manual intervention needed.

The field list and extraction rules below are based on standard US
medical billing / RCM (Revenue Cycle Management) payment posting
conventions: the BA/AA/CA/PA/PR formula set, primary vs secondary vs
tertiary posting rules, and the standard EOB/ERA "master field list".

Get a free Gemini API key at: https://aistudio.google.com  (click "Get API key")
Get a free Groq API key at:   https://console.groq.com/keys
"""

import json
import os
import re

# llama-3.3-70b-versatile is a strong free-tier model on Groq, good for
# structured extraction tasks. Override via GROQ_MODEL in .env if desired.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# gemini-2.5-flash is fast and has a generous free tier, good for
# structured extraction. Override via GEMINI_MODEL in .env if desired.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Fields the AI is asked to extract directly from the document text.
# Naming follows standard RCM abbreviations (BA/AA/CA/PA/PR1/PR2/PR3) so the
# output maps 1:1 onto the industry-standard payment posting formulas.
REQUIRED_FIELDS = [
    # Identifiers
    "insurance_name",
    "payer_level",              # Primary / Secondary / Tertiary, if determinable
    "patient_name",
    "patient_account_number",   # Account / Encounter number
    "policy_id",                # Subscriber / Policy ID
    "group_number",
    "claim_number",
    "date_of_service",          # DOS
    "cpt_code",                 # CPT / HCPCS procedure code
    "diagnosis_code",           # ICD / Dx code
    "pos",                      # Place of Service code
    # Financials — standard RCM formula fields
    "billed_amount",            # BA
    "allowed_amount",           # AA (Primary only — see posting rules below)
    "contractual_adjustment",   # CA (Primary only — see posting rules below)
    "paid_amount",              # PA
    "deductible",               # PR1
    "coinsurance",              # PR2
    "copay",                    # PR3
    "patient_responsibility",   # Total PR = PR1 + PR2 + PR3, if stated directly
    # Codes
    "denial_code",              # CARC (e.g. CO-45, CO-97)
    "remark_code",              # RARC
    # Payment info
    "check_or_eft_number",
    "check_date",
    "mode_of_payment",          # Check / EFT / Virtual Card / etc.
]

SYSTEM_PROMPT = f"""You are a medical billing / payment-posting specialist AI
trained on US healthcare Revenue Cycle Management (RCM) conventions.

You will be given the raw text (including flattened tables, and possibly
OCR output) extracted from an insurance EOB (Explanation of Benefits) or
ERA/ERN (Electronic Remittance Advice) PDF.

=== YOUR JOB ===
Extract every claim / service line as a separate JSON object. Do not
summarize or merge service lines together — if a claim has multiple CPT
codes / service lines, output one row per service line, repeating the
shared claim-level info (patient, policy id, claim number, etc.) on each row.
If the document contains multiple distinct claims (e.g. multiple patients,
or a batch ERA with several claims), include all of them as separate rows.

=== FIELD DEFINITIONS (standard RCM abbreviations) ===
- BA (billed_amount): the amount the provider billed/charged.
- AA (allowed_amount): the amount the payer's contract allows for the service.
- CA (contractual_adjustment): BA − AA — what the provider must write off per contract.
- PA (paid_amount): the amount the insurance actually paid.
- PR1 (deductible), PR2 (coinsurance), PR3 (copay): the three components of patient responsibility.
- patient_responsibility: the TOTAL patient responsibility (PR1+PR2+PR3), only if the document states a combined total directly — do not calculate it yourself, just extract what's written (or null).
- denial_code: a CARC-style code (e.g. "CO-45", "CO-97", "PR-1") explaining an adjustment or denial reason.
- remark_code: a RARC-style code or footnote code (e.g. "N130", "PDC") with additional explanation, often referenced from a legend at the bottom of the document.
- payer_level: if the document indicates this claim was processed as Primary, Secondary, or Tertiary insurance (from context like "COB", "secondary payer", explicit labels, or order of multiple EOBs for the same claim), extract that. Otherwise null — do not guess.

=== CRITICAL POSTING RULE — DO NOT TREAT AS A MISSING-FIELD PROBLEM ===
Per standard RCM posting rules:
- PRIMARY insurance claims typically show all of: BA, AA, CA, PA, PR.
- SECONDARY and TERTIARY insurance claims typically do NOT show AA or CA at all —
  only BA, PA, and PR are relevant/shown. This is NORMAL and CORRECT, not a
  missed extraction. If the document is clearly a secondary/tertiary EOB and
  doesn't mention allowed amount or contractual adjustment, set those fields
  to null confidently rather than guessing or leaving them out of the object.

=== DO NOT DO ARITHMETIC ===
Only extract values that are LITERALLY WRITTEN in the document. Do not
calculate missing amounts yourself (e.g. do not compute CA as BA-AA if CA
isn't shown) — a separate, more reliable process handles that afterward.
Your only job is faithful extraction of what's on the page.

=== REQUIRED FIELDS ===
Each object must include these fields (use null if the document truly does
not contain that field — see posting rule above for when null is expected
and correct — and DO NOT hallucinate values):
{json.dumps(REQUIRED_FIELDS, indent=2)}

Additionally, include a field called "other_fields": an object containing
any other important payment-posting information you find that doesn't fit
the fields above (e.g. provider name/NPI, subscriber DOB, remit/statement
date, interest paid, service description, timely filing reference, refund
info, etc). Only include keys that are actually present in the document.
Use short snake_case keys.

=== FORMATTING RULES ===
- date_of_service and check_date should be formatted MM/DD/YYYY if possible.
- All dollar amount fields (billed_amount, allowed_amount, contractual_adjustment, paid_amount, deductible, coinsurance, copay, patient_responsibility) should be plain numbers as strings (e.g. "125.00"), no currency symbols. Use null if not found.
- The document text may contain OCR noise/typos (it may have been scanned) — use context to correct obvious OCR errors in numbers where reasonably confident, otherwise use null.
- Respond with ONLY a single JSON object of the form {{"claims": [ ... ]}}. No markdown code fences, no preamble, no explanation, no trailing commentary.
- If you genuinely cannot find any claim/service-line data at all, return {{"claims": []}}
"""

USER_MESSAGE_PREFIX = "Here is the extracted EOB/ERA/ERN document text:\n\n"
MAX_CHARS = 100_000  # guard against blowing the model's context window


class AIExtractionError(Exception):
    pass


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _call_gemini(document_text: str) -> str:
    """Calls Gemini's free tier. Raises on any failure (missing key, network, API error)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=USER_MESSAGE_PREFIX + document_text[:MAX_CHARS],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0,
            max_output_tokens=8192,
        ),
    )

    text = response.text
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def _call_groq(document_text: str) -> str:
    """Calls Groq's free tier. Raises on any failure (missing key, network, API error)."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set.")

    from groq import Groq

    client = Groq(api_key=api_key)

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=4096,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_MESSAGE_PREFIX + document_text[:MAX_CHARS]},
        ],
    )

    text = response.choices[0].message.content or ""
    if not text:
        raise RuntimeError("Groq returned an empty response.")
    return text


def extract_payment_data(document_text: str) -> tuple[list[dict], str]:
    """
    Extracts structured payment-posting rows from document text.

    Tries Gemini first. If the Gemini call itself fails (missing key,
    network error, API error, rate limit, etc.), automatically retries
    with Groq. If both fail, raises AIExtractionError with details on both.

    Returns (rows, provider_used) where provider_used is "gemini" or "groq",
    so the caller/UI can show which one actually served the request.
    """
    raw_text = None
    provider_used = None
    gemini_error = None

    try:
        raw_text = _call_gemini(document_text)
        provider_used = "gemini"
    except Exception as e:
        gemini_error = e
        try:
            raw_text = _call_groq(document_text)
            provider_used = "groq"
        except Exception as groq_error:
            raise AIExtractionError(
                f"Both AI providers failed. Gemini: {gemini_error} | Groq: {groq_error}"
            )

    cleaned = _strip_code_fences(raw_text)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AIExtractionError(
            f"Could not parse {provider_used}'s response as JSON: {e}. "
            f"Raw response: {raw_text[:500]}"
        )

    # Model is instructed to return {"claims": [...]}, but be forgiving in
    # case it returns a bare array or a differently-named key.
    if isinstance(parsed, list):
        data = parsed
    elif isinstance(parsed, dict):
        data = parsed.get("claims")
        if data is None:
            list_values = [v for v in parsed.values() if isinstance(v, list)]
            data = list_values[0] if list_values else None
    else:
        data = None

    if not isinstance(data, list):
        raise AIExtractionError(
            f"{provider_used}'s response did not contain a claims array as expected."
        )

    # Make sure every required field key exists on every row.
    normalized = []
    for row in data:
        if not isinstance(row, dict):
            continue
        clean_row = {field: row.get(field) for field in REQUIRED_FIELDS}
        clean_row["other_fields"] = row.get("other_fields") or {}
        normalized.append(clean_row)

    return normalized, provider_used
