// Set in config.js. Empty string = same origin (local dev via FastAPI).
// Set to your deployed Render backend URL when hosting frontend on Vercel.
const API_BASE = (window.APP_CONFIG && window.APP_CONFIG.API_BASE) || "";

const form = document.getElementById("upload-form");
const dropZone = document.getElementById("drop-zone");
const dropZoneText = document.getElementById("drop-zone-text");
const pdfInput = document.getElementById("pdf-input");
const submitBtn = document.getElementById("submit-btn");
const statusEl = document.getElementById("status");
const resultsSection = document.getElementById("results-section");
const resultsTable = document.getElementById("results-table");
const rowCountEl = document.getElementById("row-count");
const downloadBtn = document.getElementById("download-csv-btn");

const FIXED_COLUMNS = [
  { key: "insurance_name", label: "Insurance" },
  { key: "payer_level", label: "Payer Level" },
  { key: "patient_name", label: "Patient" },
  { key: "patient_account_number", label: "Account #" },
  { key: "policy_id", label: "Policy ID" },
  { key: "group_number", label: "Group #" },
  { key: "claim_number", label: "Claim #" },
  { key: "date_of_service", label: "DOS" },
  { key: "cpt_code", label: "CPT" },
  { key: "diagnosis_code", label: "Dx" },
  { key: "pos", label: "POS" },
  { key: "billed_amount", label: "Billed (BA)" },
  { key: "allowed_amount", label: "Allowed (AA)" },
  { key: "contractual_adjustment", label: "Contractual (CA)" },
  { key: "paid_amount", label: "Paid (PA)" },
  { key: "deductible", label: "Deductible" },
  { key: "coinsurance", label: "Coinsurance" },
  { key: "copay", label: "Copay" },
  { key: "patient_responsibility", label: "Patient Resp (PR)" },
  { key: "claim_status", label: "Status" },
  { key: "denial_code", label: "Denial Code" },
  { key: "remark_code", label: "Remark Code" },
  { key: "check_or_eft_number", label: "Check/EFT #" },
  { key: "check_date", label: "Check Date" },
  { key: "mode_of_payment", label: "Payment Mode" },
];

let lastRows = [];
let lastOtherKeys = [];

dropZone.addEventListener("click", () => pdfInput.click());

["dragover", "dragenter"].forEach((evt) =>
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  })
);

["dragleave", "drop"].forEach((evt) =>
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
  })
);

dropZone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) {
    pdfInput.files = e.dataTransfer.files;
    updateDropZoneLabel(file);
  }
});

pdfInput.addEventListener("change", () => {
  const file = pdfInput.files[0];
  if (file) updateDropZoneLabel(file);
});

function updateDropZoneLabel(file) {
  dropZoneText.textContent = `Selected: ${file.name}`;
  submitBtn.disabled = false;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = pdfInput.files[0];
  if (!file) return;

  setStatus("Uploading and analyzing document… this can take up to a minute.", "");
  submitBtn.disabled = true;
  resultsSection.classList.add("hidden");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`${API_BASE}/api/extract`, {
      method: "POST",
      body: formData,
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Extraction failed.");
    }

    lastRows = data.rows || [];
    renderResults(lastRows);
    const providerLabel = data.ai_provider_used === "groq" ? "Groq (fallback)" : "Gemini";
    setStatus(
      `Done. Extracted ${data.row_count} row(s) from "${data.filename}" using ${providerLabel}.`,
      "success"
    );
  } catch (err) {
    setStatus(`Error: ${err.message}`, "error");
  } finally {
    submitBtn.disabled = false;
  }
});

function setStatus(msg, type) {
  statusEl.textContent = msg;
  statusEl.className = `status ${type}`;
}

function renderResults(rows) {
  const thead = resultsTable.querySelector("thead");
  const tbody = resultsTable.querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";

  if (!rows.length) {
    resultsSection.classList.remove("hidden");
    rowCountEl.textContent = "0 rows";
    tbody.innerHTML = `<tr><td colspan="${FIXED_COLUMNS.length + 1}">No claim/service-line data found in this document.</td></tr>`;
    return;
  }

  // Gather union of "other_fields" keys across all rows, in first-seen order.
  const otherKeysSet = new Set();
  rows.forEach((row) => {
    Object.keys(row.other_fields || {}).forEach((k) => otherKeysSet.add(k));
  });
  lastOtherKeys = Array.from(otherKeysSet);

  // Header row
  const headerRow = document.createElement("tr");
  FIXED_COLUMNS.forEach((col) => {
    const th = document.createElement("th");
    th.textContent = col.label;
    headerRow.appendChild(th);
  });
  const reviewTh = document.createElement("th");
  reviewTh.textContent = "Review";
  headerRow.appendChild(reviewTh);
  lastOtherKeys.forEach((key) => {
    const th = document.createElement("th");
    th.textContent = prettifyKey(key);
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);

  // Body rows
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (row.needs_review) tr.classList.add("needs-review");

    FIXED_COLUMNS.forEach((col) => {
      const td = document.createElement("td");
      if (col.key === "claim_status") {
        appendStatusCell(td, row[col.key]);
      } else {
        appendCellValue(td, row[col.key]);
      }
      tr.appendChild(td);
    });

    const reviewTd = document.createElement("td");
    if (row.needs_review) {
      reviewTd.textContent = "⚠️ Check";
      reviewTd.classList.add("review-flag");
      reviewTd.title = (row.review_notes || []).join("\n");
    } else {
      reviewTd.textContent = "✓";
      reviewTd.classList.add("review-ok");
    }
    tr.appendChild(reviewTd);

    lastOtherKeys.forEach((key) => {
      const td = document.createElement("td");
      appendCellValue(td, (row.other_fields || {})[key]);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  rowCountEl.textContent = `${rows.length} row${rows.length === 1 ? "" : "s"}`;
  resultsSection.classList.remove("hidden");
}

function appendStatusCell(td, value) {
  if (!value) {
    td.textContent = "—";
    td.classList.add("null-value");
    return;
  }
  td.textContent = value;
  td.classList.add(value === "PAID" ? "status-paid" : "status-denied");
}

function appendCellValue(td, value) {
  if (value === null || value === undefined || value === "") {
    td.textContent = "—";
    td.classList.add("null-value");
  } else {
    td.textContent = value;
  }
}

function prettifyKey(key) {
  return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

downloadBtn.addEventListener("click", () => {
  if (!lastRows.length) return;

  const headers = [
    ...FIXED_COLUMNS.map((c) => c.label),
    "Needs Review",
    "Review Notes",
    ...lastOtherKeys.map(prettifyKey),
  ];
  const csvRows = [headers.join(",")];

  lastRows.forEach((row) => {
    const values = [
      ...FIXED_COLUMNS.map((c) => csvEscape(row[c.key])),
      csvEscape(row.needs_review ? "YES" : "NO"),
      csvEscape((row.review_notes || []).join("; ")),
      ...lastOtherKeys.map((k) => csvEscape((row.other_fields || {})[k])),
    ];
    csvRows.push(values.join(","));
  });

  const blob = new Blob([csvRows.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "extracted_payment_data.csv";
  a.click();
  URL.revokeObjectURL(url);
});

function csvEscape(value) {
  if (value === null || value === undefined) return "";
  const str = String(value).replace(/"/g, '""');
  return /[",\n]/.test(str) ? `"${str}"` : str;
}
