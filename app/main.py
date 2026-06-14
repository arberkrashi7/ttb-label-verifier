"""TTB Label Verifier — FastAPI application entrypoint.

Serves the single-page frontend and the API: read a label image with Claude
Vision (`/api/extract`) and compare it to the application values (`/api/verify`).
"""

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.extractor import ExtractionError, extract_label_fields, prewarm
from app.verifier import ApplicationValues, verify

load_dotenv()

logger = logging.getLogger("ttb")

# Reject oversized uploads before we read them into memory.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup, warm the extraction schema in the background (set
    PREWARM_SCHEMA=0 to disable)."""
    if os.getenv("PREWARM_SCHEMA", "1") != "0":
        threading.Thread(target=prewarm, daemon=True).start()
    yield

# Project layout: this file lives in app/, the frontend lives in static/.
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="TTB Label Verifier", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    """Turn FastAPI's raw 422 validation errors into a friendly message."""
    return JSONResponse(
        status_code=400,
        content={"error": "Please add a label image and try again."},
    )


@app.exception_handler(Exception)
async def on_unexpected_error(request: Request, exc: Exception):
    """Backstop: never leak a stack trace; always return a friendly message."""
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong on our end. Please try again."},
    )


@app.get("/health")
def health() -> dict:
    """Liveness probe used locally and by the deployment platform."""
    return {"status": "ok", "service": "ttb-label-verifier", "version": app.version}


@app.get("/")
def index() -> FileResponse:
    """Serve the single main screen."""
    return FileResponse(STATIC_DIR / "index.html")


async def _read_upload(image: UploadFile) -> bytes:
    """Validate and read an uploaded image, or raise ValueError with a message."""
    if not (image.content_type or "").startswith("image/"):
        raise ValueError("Please upload an image file (JPG or PNG).")
    raw = await image.read()
    if not raw:
        raise ValueError("The image was empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("That image is too large. Please use one under 15 MB.")
    return raw


@app.post("/api/extract")
async def extract(image: UploadFile = File(...)) -> JSONResponse:
    """Read an uploaded label image and return the extracted fields as JSON."""
    try:
        raw = await _read_upload(image)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    started = time.perf_counter()
    try:
        fields = extract_label_fields(raw)
    except ExtractionError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception:
        logger.exception("Unexpected error reading label")
        return JSONResponse(
            status_code=502,
            content={"error": "We couldn't read that image. Please try a clearer photo."},
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return JSONResponse(
        content={"fields": fields.model_dump(), "elapsed_ms": elapsed_ms}
    )


@app.post("/api/verify")
async def verify_label(
    image: UploadFile = File(...),
    brand_name: str = Form(""),
    class_type: str = Form(""),
    alcohol_content: str = Form(""),
    net_contents: str = Form(""),
    government_warning: str = Form(""),
) -> JSONResponse:
    """Read the label, compare it to the application values, return verdicts."""
    try:
        raw = await _read_upload(image)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    started = time.perf_counter()
    try:
        fields = extract_label_fields(raw)
    except ExtractionError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception:
        logger.exception("Unexpected error reading label")
        return JSONResponse(
            status_code=502,
            content={"error": "We couldn't read that image. Please try a clearer photo."},
        )

    try:
        application = ApplicationValues(
            brand_name=brand_name,
            class_type=class_type,
            alcohol_content=alcohol_content,
            net_contents=net_contents,
            government_warning=government_warning,
        )
        result = verify(fields, application)
    except Exception:
        logger.exception("Unexpected error comparing fields")
        return JSONResponse(
            status_code=500,
            content={"error": "Something went wrong while checking the label. Please try again."},
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return JSONResponse(
        content={
            "extracted": fields.model_dump(),
            "verification": result.model_dump(),
            "elapsed_ms": elapsed_ms,
        }
    )


# Serve CSS/JS/assets. Mounted last so it doesn't shadow the routes above.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
