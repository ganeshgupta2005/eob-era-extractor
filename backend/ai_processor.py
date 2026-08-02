"""
ai_processor.py
----------------
Sends extracted EOB/ERA/ERN text to a free AI model via the Groq API
(OpenAI-compatible, generous free tier) and asks it to return structured
payment-posting data as JSON, with one entry per claim / service line.

Get a free Groq API key at: https://console.groq.com/keys
"""

import json
import os
import re

from groq import Groq

# llama-3.3-70b-versatile is a strong free-tier model on Groq, good for
# structured extraction tasks. Override via GROQ_MODEL in .env if desired.
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

REQUIRED_FIELDS = [
    "insurance_name",
    "patient_name",
    "policy_id",
    "invoice_number",
    "claim_number",
    "date_of_service",
    "cpt_code",
    "charge_amount",
    "paid_amount",
    "check_or_eft_number",
]

SYSTEM_PROMPT = f"""You are a medical billing / payment-posting specialist AI.
You will be given the raw text (including flattened tables) extracted from
an insurance EOB (Explanation of Benefits) or ERA/ERN (Electronic
Remittance Advice) PDF.

Your job: extract every claim / service line as a separate JSON object. Do
not summarize or merge service lines together — if a claim has multiple
CPT codes / service lines, output one row per service line, repeating the
shared claim-level info (patient, policy id, claim number, etc.) on each row.

Each object must include these fields (use null if the document truly does
not contain that field, and DO NOT hallucinate values):
{json.dumps(REQUIRED_FIELDS, indent=2)}

Additionally, include a field called "other_fields": an object containing
any other important payment-posting information you find that doesn't fit
the fields above (e.g. adjustment codes / reason codes (CARC/RARC),
allowed amount, deductible, coinsurance, copay, patient responsibility,
provider name/NPI, group number, remit date, interest paid, etc). Only
include keys that are actually present in the document. Use short
snake_case keys.

Formatting rules:
- date_of_service should be formatted MM/DD/YYYY if possible.
- charge_amount and paid_amount should be plain numbers as strings (e.g. "125.00"), no currency symbols. Use null if not found.
- The document text may contain OCR noise/typos (it may have been scanned) — use context to correct obvious OCR errors in numbers where reasonably confident, otherwise use null.
- Respond with ONLY a single JSON object of the form {{"claims": [ ... ]}}, where the array contains one object per claim/service line as described above. No markdown code fences, no preamble, no explanation, no trailing commentary.
- If the document contains multiple distinct claims, include all of them.
- If you genuinely cannot find any claim/service-line data at all, return {{"claims": []}}
"""


class AIExtractionError(Exception):
    pass


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_payment_data(document_text: str) -> list[dict]:
    """
    Calls the Groq API (free tier) with the document text and returns a
    list of dicts, one per claim/service line.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise AIExtractionError(
            "GROQ_API_KEY is not set. Get a free key at "
            "https://console.groq.com/keys and add it to your .env file."
        )

    client = Groq(api_key=api_key)

    # Guard against extremely large documents blowing the context window.
    max_chars = 100_000
    truncated = document_text[:max_chars]

    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=4096,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Here is the extracted EOB/ERA/ERN document text:\n\n"
                        f"{truncated}"
                    ),
                },
            ],
        )
    except Exception as e:
        raise AIExtractionError(f"Groq API call failed: {e}")

    raw_text = response.choices[0].message.content or ""
    cleaned = _strip_code_fences(raw_text)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AIExtractionError(
            f"Could not parse AI response as JSON: {e}. Raw response: {raw_text[:500]}"
        )

    # Model is instructed to return {"claims": [...]}, but be forgiving in
    # case it returns a bare array or a differently-named key.
    if isinstance(parsed, list):
        data = parsed
    elif isinstance(parsed, dict):
        data = parsed.get("claims")
        if data is None:
            # Fall back to the first list value found in the object
            list_values = [v for v in parsed.values() if isinstance(v, list)]
            data = list_values[0] if list_values else None
    else:
        data = None

    if not isinstance(data, list):
        raise AIExtractionError("AI response did not contain a claims array as expected.")

    # Make sure every required field key exists on every row.
    normalized = []
    for row in data:
        if not isinstance(row, dict):
            continue
        clean_row = {field: row.get(field) for field in REQUIRED_FIELDS}
        clean_row["other_fields"] = row.get("other_fields") or {}
        normalized.append(clean_row)

    return normalized
