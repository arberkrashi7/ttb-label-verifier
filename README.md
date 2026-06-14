# TTB Label Verifier

A simple web app that helps a compliance agent check whether an **alcohol label
image** matches the **application field values** submitted for it (TTB = Alcohol
and Tobacco Tax and Trade Bureau).

The agent uploads a label photo and types in the application values (brand name,
class/type, alcohol content, net contents, government warning). The app reads the
label with Claude's vision model and returns a clear, color-coded checklist:
**PASS / FAIL / NEEDS REVIEW / CANNOT VERIFY** for each field, with a
confidence level and a plain-language reason.

The interface is intentionally minimal with large fonts, high contrast, big
buttons, one screen.

---

## Tech stack

- **Backend:** Python + [FastAPI](https://fastapi.tiangolo.com/) (serves the page
  and a small JSON API)
- **Label reading:** [Claude vision API](https://www.anthropic.com/) — Claude
  Haiku 4.5 by default (fast), with structured JSON output
- **Image prep:** [Pillow](https://python-pillow.org/) (downscale before upload)
- **Matching:** Python standard library + [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)
  for fuzzy text comparison
- **Frontend:** plain HTML + CSS + vanilla JavaScript (no framework, no build step)
- **Deployment target:** Render

---

## Project structure

```
ttb-label-verifier/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI app: routes, error handling, startup pre-warm
│   ├── extractor.py       # Claude Vision: image -> structured label fields
│   └── verifier.py        # comparison logic: fields -> PASS/FAIL/REVIEW/CANNOT VERIFY
├── static/
│   ├── index.html         # the single main screen
│   ├── style.css          # large-type, high-contrast styling
│   └── app.js             # upload, submit, render the checklist
├── scripts/
│   └── make_sample_labels.py   # generates the test images in samples/
├── samples/               # five ready-made test labels (see Testing below)
├── requirements.txt
├── render.yaml            # Render deploy blueprint
├── .env.example           # copy to .env and add your API key
├── .gitignore
├── LICENSE
└── README.md
```

---

## Setup and run (local)

Requires **Python 3.10+** (developed on 3.14).

1. **Create and activate a virtual environment.**

   **Windows (PowerShell):**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   > If activation is blocked, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then retry.

   **macOS / Linux:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install dependencies.**
   ```bash
   pip install -r requirements.txt
   ```

3. **Add your API key.** Copy the example file and paste your real key into it:
   ```bash
   cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
   ```
   Then edit `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...your key...
   ```
   Get a key from the [Anthropic Console](https://platform.claude.com/). The
   `.env` file is git-ignored.

4. **Start the server.**
   ```bash
   uvicorn app.main:app --reload
   ```
   (If `uvicorn` isn't on your PATH, use `python -m uvicorn app.main:app --reload`.)

5. **Open** [http://127.0.0.1:8000](http://127.0.0.1:8000).

A health check is available at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

### Configuration (environment variables)

| Variable             | Default            | Purpose                                                        |
| -------------------- | ------------------ | ------------------------------------------------------------- |
| `ANTHROPIC_API_KEY`  | *(required)*       | Your Claude API key.                                          |
| `ANTHROPIC_MODEL`    | `claude-haiku-4-5` | Vision model. Set to `claude-opus-4-8` for max accuracy (slower). |
| `PREWARM_SCHEMA`     | `1`                | Warm the output schema at startup; set `0` to disable.       |

---

## Testing with sample labels

Five ready-made test images live in `samples/` (regenerate them anytime with
`python scripts/make_sample_labels.py`). Each exercises a different outcome:

| File                | What to expect                                            |
| ------------------- | -------------------------------------------------------- |
| `01_pass.png`       | Everything matches → **PASS**                            |
| `02_brand_case.png` | Brand differs only by case → **PASS** (case ignored)    |
| `03_wrong_abv.png`  | Label ABV ≠ entered value → **FAIL** on ABV             |
| `04_bad_warning.png`| Warning header in Title Case → **FAIL** on the warning  |
| `05_blurry.png`     | Blur + glare → **CANNOT VERIFY** on the unreadable fields |

The exact values to type into the form for each image are printed when you run
`python scripts/make_sample_labels.py`.

The Government Warning is federally mandated and identical on every U.S. product,
so for a PASS, paste this verbatim into the Government Warning field:

```
GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.
```

---

## How it works (approach)

1. **Upload + prep.** The browser sends the image and the typed application
   values to `POST /api/verify`. The server validates the file (type, size) and
   downscales the image to ≤1568px on the long edge, re-encoded as JPEG, to keep
   the upload small and fast.
2. **Read the label.** `extractor.py` sends the image to Claude's vision model
   and uses **structured output** (`messages.parse` against a fixed schema) so
   the model returns exactly five fields as validated JSON — no fragile text
   parsing. The model is explicitly told **not to guess**: any field it can't
   read confidently (blur, glare, cut-off) is left blank and flagged.
3. **Compare.** `verifier.py` compares each extracted field to the application
   value with rules tuned per field type (see below) and returns a verdict,
   confidence, and reason.
4. **Show results.** The frontend renders a scannable checklist with a colored
   icon per field and an overall status banner at the top.

### Matching rules per field

| Field                          | Rule                                                                                   |
| ------------------------------ | -------------------------------------------------------------------------------------- |
| Brand name, Class/Type, Net contents | Fuzzy match (RapidFuzz), ignoring case and punctuation. Close match → **PASS**; a real difference → **NEEDS REVIEW** (a human decides). |
| Alcohol content (ABV)          | Parse the number from both sides (handles `%`, `Alc/Vol`, and `Proof`). Within ±0.1 → **PASS**; otherwise **FAIL**. |
| Government warning             | **Strict.** Requires an exact text match **and** the literal `GOVERNMENT WARNING` in all caps. Title case (e.g. "Government Warning") → **FAIL**. |
| Any field flagged unreadable   | **CANNOT VERIFY — request better image** (never a guessed verdict).                     |

The **overall** status is the most severe outcome across all fields
(FAIL > CANNOT VERIFY > NEEDS REVIEW > PASS).

### Error handling

Bad file types, missing images, oversized uploads, unreadable images, invalid
API keys, timeouts, lost connections, and any unexpected server error all return
a clear, friendly message, and never a stack trace. Errors appear as a bordered
red banner at the top of the page; real errors are logged server-side for
debugging.

---

## Deploying to Render

This repo includes a `render.yaml` blueprint, so deployment is a few clicks: in
the [Render](https://render.com) dashboard choose **New + → Blueprint**, connect
this repo, and when prompted paste your `ANTHROPIC_API_KEY`. It is stored as an
encrypted secret on the host — never committed to the repo. Render then runs:

- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Python:** pinned to 3.12 via the blueprint.

> **Cold starts (free tier):** warm requests complete in under 5 seconds, but the
> free Render instance sleeps after ~15 minutes of inactivity. The first visit
> after it has slept takes ~30–60 seconds to wake — just click once to wake it,
> then it stays responsive. A paid always-on instance removes this.

---

## Assumptions & Tradeoffs

**Speed: the 5-second target.** A single label round-trip is designed to finish
in under 5 seconds, and measures ~2–3 seconds in practice. The design choices
that get there:

- **Model choice.** The default is **Claude Haiku 4.5**, the fast, vision-capable
  tier — appropriate for a simple, well-scoped extraction. (Opus would be more
  accurate but routinely exceed the budget; it's available via `ANTHROPIC_MODEL`
  if accuracy matters more than latency for a given deployment.)
- **Smaller payloads.** Images are downscaled to ≤1568px and re-encoded as JPEG
  before upload, which is the single biggest latency lever for phone photos.
- **Constrained output.** Output is capped (small `max_tokens`) and shaped by a
  fixed schema, so the model returns a compact JSON object quickly.
- **No cold start.** Structured-output schemas are compiled on first use and
  cached (~24h). The server fires one tiny throwaway request at startup to warm
  that cache, so the first real user doesn't pay the one-time compilation cost
  (~7s when cold vs ~2.5s warm).
- **Fail fast.** The API client has a bounded timeout so a slow call returns a
  friendly error instead of hanging.

**This is a standalone prototype, not integrated with COLA.** It is a focused
demonstration of the read-and-compare workflow. It does **not** connect to TTB's
[COLA](https://www.ttb.gov/labeling) (Certificate of Label Approval) system or
COLAs Online, has no authentication, no database, no audit trail, and no
submission/approval workflow. Application values are typed in manually rather
than pulled from an existing COLA record. Verdicts are decision support for a
human reviewer, not an automated approval.

**A production federal deployment would look different.** Several realities of
deploying inside a federal agency would reshape the architecture:

- **Outbound API calls / firewall restrictions.** Government networks typically
  deny-by-default on egress. A server that needs to reach `api.anthropic.com`
  over the public internet would likely be blocked, and getting an allow-listed
  egress path approved is non-trivial.
- **PII and data-retention rules.** Label and application data can include
  business-sensitive and personal information. Sending that to a third-party
  cloud API raises data-handling, retention, and records-management questions
  that must be answered before any such call is permitted.
- **FedRAMP.** Cloud services used by federal agencies generally must hold a
  FedRAMP authorization at the appropriate impact level. An un-authorized SaaS
  API is usually a non-starter for production use.

  Because of the above, a production version would likely **avoid a public cloud
  API in the request path.** Realistic options include **on-prem / in-boundary
  OCR** (e.g. a self-hosted OCR engine) paired with a **locally hosted model**
  for the field extraction and matching, or routing inference through a
  FedRAMP-authorized environment (e.g. a government cloud region). The
  application is structured to make that swap localized: `extractor.py` is the
  only component that talks to the model, so the reading step could be replaced
  with an on-prem OCR + local-model implementation without touching the
  comparison logic, API, or UI.

**Other tradeoffs worth noting:**

- The **government-warning check is deliberately strict** (exact text + all-caps).
  This is the safest default for a compliance check, but it means legitimate
  whitespace/formatting differences in how the warning was *entered* can require
  a human to confirm. (Line-wrapping differences are normalized; case and wording
  are not.)
- The fuzzy fields **never auto-FAIL** — a real difference becomes NEEDS REVIEW,
  keeping a human in the loop rather than rejecting on an OCR/reading hiccup.
- **CANNOT VERIFY relies on the model self-reporting** that a field was
  unreadable. This works well in practice but is a heuristic, not a calibrated
  confidence score.
- Sample test labels are **synthetic** (generated by `scripts/make_sample_labels.py`)
  so the exact warning text can be controlled; real photos add OCR variability.
