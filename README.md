# EOB / ERA Payment Posting Extractor

An AI-powered web app that lets a user upload an insurance **EOB** or
**ERA/ERN** PDF, extracts the text, and uses a **free AI model (via Groq)**
to pull out structured payment-posting data into a table (one row per
claim / service line).

## How it works
1. User uploads a PDF in the browser.
2. FastAPI backend extracts raw text (and tables) from the PDF using `pdfplumber`.
   Pages with no embedded text layer (i.e. scanned/image-based EOBs) are
   automatically OCR'd using `pytesseract` + `pdf2image` as a fallback.
3. The extracted text is sent to the **Groq API** (free tier, `llama-3.3-70b-versatile`
   by default) with instructions to return structured JSON: one object per
   claim/service line, containing the required fields plus any other
   important payment-posting details it finds.
4. The frontend renders the JSON as a table, with an option to download as CSV.

## Tested against
This has been verified end-to-end (PDF text/OCR extraction) against:
- A native text-based EOB (CMS sample document)
- A scanned/image-based EOB (BlueCross BlueShield of Texas sample) — this
  one required the OCR fallback path, which worked correctly.

## Project structure
```
eob-era-extractor/
├── backend/
│   ├── main.py            # FastAPI app + API routes
│   ├── pdf_processor.py   # PDF text extraction + OCR fallback
│   ├── ai_processor.py    # Groq API call + JSON parsing
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html
    ├── style.css
    └── script.js
```

## Setup

### 1. Install system dependency: Tesseract OCR + Poppler
These are needed for the OCR fallback (scanned/image-based EOBs), used via
`pytesseract` and `pdf2image`.

- **Mac:** `brew install tesseract poppler`
- **Ubuntu/Debian:** `sudo apt-get install tesseract-ocr poppler-utils`
- **Windows:** install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
  and [Poppler for Windows](https://github.com/oschwartz10612/poppler-windows/releases),
  then add both to your PATH.

If you skip this, the app still works fine for normal text-based PDFs — OCR
is only used as a fallback for scanned pages.

### 2. Install Python dependencies
```bash
cd backend
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Add your free Groq API key
```bash
cp .env.example .env
```
Get a **free** key at https://console.groq.com/keys (no credit card needed),
then edit `.env` and paste it in:
```
GROQ_API_KEY=gsk_...
```

### 4. Run the server
```bash
uvicorn main:app --reload --port 8000
```

### 5. Open the app
Go to **http://localhost:8000** in your browser. The FastAPI backend also
serves the frontend directly, so you don't need a separate frontend server.

(If you'd rather run the frontend separately — e.g. with `python -m http.server`
from the `frontend/` folder — just update `API_BASE` in `script.js` to point
at `http://localhost:8000`.)

## Deploying (Vercel + Render)

Recommended split: **frontend on Vercel**, **backend on Render** (Render
supports Docker, so Tesseract/Poppler for OCR install cleanly, and there's
no hard request timeout like on Vercel's serverless functions).

### 1. Push this project to a GitHub repo
Vercel and Render both deploy from a Git repo.

### 2. Deploy the backend on Render
1. Go to https://render.com → New → Web Service → connect your repo.
2. Render will detect `render.yaml` at the project root and use it
   automatically (env: docker, free plan, points at `backend/Dockerfile`).
   If it doesn't auto-detect, set:
   - **Root/Dockerfile path:** `backend/Dockerfile`
   - **Docker context:** `backend`
3. Add your environment variable: `GROQ_API_KEY` = your free Groq key.
4. Deploy. Once live, copy the backend URL Render gives you, e.g.
   `https://eob-era-extractor-backend.onrender.com`.

*Note: Render's free plan spins the service down after inactivity, so the
first request after idling can take ~30–60s to "wake up." That's normal.*

### 3. Deploy the frontend on Vercel
1. Go to https://vercel.com → New Project → import the same repo.
2. Set **Root Directory** to `frontend`.
3. Framework preset: "Other" (it's plain static HTML/CSS/JS, no build step needed).
4. Deploy.

### 4. Connect frontend to backend
Edit `frontend/config.js`:
```js
window.APP_CONFIG = {
  API_BASE: "https://your-backend-url.onrender.com",
};
```
Commit and push — Vercel will auto-redeploy. Your frontend will now call
your Render backend from the browser (CORS is already open on the backend).

## Notes / limitations
- Handles both **native text-based PDFs** and **scanned/image-based PDFs**
  (via automatic OCR fallback per-page).
- OCR accuracy depends on scan quality — the AI is prompted to correct
  obvious OCR noise in numbers where it's confident, but very poor scans
  may produce imperfect results.
- The AI is instructed not to hallucinate values — fields it can't find
  are returned as `null` and shown as "—" in the table.
- Max upload size is 20MB by default (`MAX_FILE_SIZE_MB` in `main.py`).
- **Groq free tier** has rate limits (requests/tokens per minute) that
  reset regularly — fine for individual/demo use, but if you hit limits
  during heavy testing, just wait a minute or check your usage at
  https://console.groq.com.
- The model used is set by `GROQ_MODEL` in `.env` (defaults to
  `llama-3.3-70b-versatile`). Groq also offers other free models (e.g.
  `llama-3.1-8b-instant` for speed, or `qwen`/`deepseek` variants) if you
  want to experiment.
- This is a general-purpose extraction prompt — if you have more real
  sample EOB/ERA PDFs from your target payers, I can tune the prompt/field
  list further for their specific formats.

## Possible next steps
- Add authentication if this will handle real PHI (this app currently has none).
- Persist results to a database instead of just showing them in-browser.
- Export to Excel (.xlsx) in addition to CSV.
- Batch upload support for multiple PDFs at once.
- Add a confidence/review flag for OCR-derived rows so users know to double-check them.
