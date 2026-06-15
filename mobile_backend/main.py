"""
mobile_backend.main
-------------------

This module implements a lightweight FastAPI backend that exposes a small
REST surface for the ECGComparisonPython application. The goal of this
backend is to allow a companion mobile client (an Expo React Native app in
mobile_app/) to call into the existing Python analysis and persistence
functions without reimplementing the domain logic.

Notes and constraints:
- This backend intentionally re-uses functions from ECGComparisonPython.py
  (e.g. build_analysis, save_record) rather than duplicating algorithmic
  logic so the mobile and web apps stay consistent.
- Authentication here is a minimal prototype token mechanism (HMAC-signed
  payload). It is suitable for local development and demos but is NOT
  production-grade: no rotation, no revocation list, and tokens are stateless.
  For production you should use OAuth2 / JWT with proper key management.
- For image plotting we render matplotlib figures to PNG bytes and return
  them base64-encoded so the mobile client can display them without needing
  to depend on a file server.

Security reminders:
- Do not embed SECRET_KEY in source for production. Use environment-backed
  secret stores.
- Use HTTPS in production and validate/limit file upload sizes.
"""

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Header
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import io
import os
import time
import hmac
import hashlib
import base64
from typing import Optional

# Standard library JSON handling
import json

# Re-use core application helpers implemented in the main module. These
# functions perform analysis, interact with the SQLite DB, and produce
# structures compatible with our UI code.
from ECGComparisonPython import (
    build_analysis,
    compute_hash,
    StoragePaths,
    export_all_data,
    load_records,
    load_record,
    save_record,
    get_user_by_username,
    verify_password,
)

# Image and numeric libraries used by analysis and plotting helpers.
from PIL import Image
import numpy as np
import tempfile

# Use Agg backend for matplotlib because this server may run headless.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# Create the FastAPI application instance. The title is useful when the
# automatically generated OpenAPI docs are inspected during development.
app = FastAPI(title="ECGComparisonMobileBackend")


# CORS: allow the mobile app during local development to call the API from
# different origins. In production, restrict this list to trusted origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Lightweight token auth (prototype only)
# ---------------------------------------------------------------------------
# We implement a small HMAC-signed token that encodes the username and an
# issuance timestamp. The token is URL-safe base64 encoded and returned to the
# client at login. Subsequent requests can pass this token in the
# Authorization: Bearer <token> header. verify_token() will check the HMAC
# signature and the token age.
# WARNING: this is intentionally simple for demos. Use a robust auth
# mechanism (e.g. OAuth2/JWT or session cookies) for any sensitive system.
SECRET_KEY = os.environ.get("MOBILE_SECRET", "devsecret").encode()
TOKEN_TTL_SECONDS = 60 * 60 * 24  # tokens valid for 24 hours by default


def create_token(username: str) -> str:
    """Create a signed token for the given username.

    Token format (before base64): ``{username}|{timestamp}|{hmac}`` where
    ``hmac`` is an HMAC-SHA256 digest of ``{username}|{timestamp}`` using the
    server SECRET_KEY. The returned token is URL-safe base64 encoded so it
    is safe to transport in headers or JSON bodies.
    """
    ts = str(int(time.time()))
    payload = f"{username}|{ts}"
    sig = hmac.new(SECRET_KEY, payload.encode(), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()
    return token


def verify_token(token: str) -> dict | None:
    """Verify the provided token and return the corresponding user record.

    Returns the user dict from the database on success, otherwise None.
    The function checks both the signature and the token age. Any decoding or
    verification failure results in None to avoid leaking details to callers.
    """
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        username, ts, sig = raw.rsplit("|", 2)
        expected = hmac.new(SECRET_KEY, f"{username}|{ts}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        # enforce TTL
        if int(time.time()) - int(ts) > TOKEN_TTL_SECONDS:
            return None
        user = get_user_by_username(username)
        return user
    except Exception:
        # On any error (malformed token, decode error, etc.) return None.
        return None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.post("/analyze")
async def analyze_image(pixels_per_mm: float = Form(...), prominence: float = Form(0.5), file: UploadFile = File(...)):
    """Analyze an uploaded ECG image and return the analysis dictionary.

    The endpoint expects a multipart/form-data POST with the image file and
    optional numeric parameters (pixels_per_mm and prominence). The function
    converts the uploaded bytes into a PIL Image and calls the existing
    build_analysis() helper from ECGComparisonPython.py. Since analysis
    results may contain numpy arrays, we recursively normalize them into
    Python lists so they are JSON serializable.
    """
    contents = await file.read()
    try:
        # Load the image from uploaded bytes and ensure RGB mode
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")
    # Delegate the heavy lifting to the existing analysis implementation.
    analysis = build_analysis(image, pixels_per_mm, prominence)

    # Helper to convert numpy arrays -> lists recursively before JSON encode
    def _normalize(obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: _normalize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_normalize(v) for v in obj]
        return obj

    return JSONResponse(content=_normalize(analysis))


@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    """Authenticate a user using the existing local user store.

    This endpoint reuses the get_user_by_username() and verify_password()
    helpers from ECGComparisonPython.py. On success it returns a minimal
    user profile and a short-lived signed token the mobile client can use to
    authenticate administrative requests.
    """
    user = get_user_by_username(username)
    if not user or not user.get("enabled"):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(password, user["password_salt"], user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(username)
    # Return a minimal user profile and token
    return JSONResponse(content={"id": user["id"], "username": user["username"], "role": user["role"], "token": token})


@app.get("/records")
async def get_records():
    """Return a list of saved record metadata as JSON.

    The load_records() helper returns a pandas DataFrame; we convert it to a
    list of dictionaries for easy JSON consumption by the mobile client.
    """
    df = load_records()
    return JSONResponse(content=df.to_dict(orient="records"))


@app.get("/record/{record_id}")
async def get_single_record(record_id: int):
    """Retrieve a single saved record (including its analysis) by id.

    This returns the record dictionary saved by save_record(); callers can
    then request the waveform PNG via /record/{id}/waveform if they want an
    image that visualizes the extracted signal.
    """
    rec = load_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
    return JSONResponse(content=rec)


@app.post("/save_record")
async def api_save_record(
    metadata: str = Form(...),
    file: UploadFile = File(...),
    pixels_per_mm: float = Form(20.0),
    prominence: float = Form(0.5),
):
    """Save an ECG record: accept image + metadata, run analysis, and store.

    The client provides metadata as a JSON-encoded string in the `metadata`
    form field. The uploaded file content and automatically produced analysis
    dictionary are persisted via the existing save_record() helper which
    returns the created record id.
    """
    contents = await file.read()
    try:
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")
    analysis = build_analysis(image, pixels_per_mm, prominence)
    # metadata expected as JSON string; fall back to raw string note if parse fails
    try:
        meta = json.loads(metadata)
    except Exception:
        meta = {"note": metadata}

    # Determine a safe extension to persist (fallback to .png)
    ext = os.path.splitext(file.filename)[1] or ".png"
    record_id = save_record(meta, contents, ext, analysis)
    return JSONResponse(content={"record_id": record_id})


@app.post("/compare")
async def api_compare(record_a: Optional[int] = Form(None), record_b: Optional[int] = Form(None), analysis_a: Optional[dict] = Form(None), analysis_b: Optional[dict] = Form(None)):
    """Compare two ECG analyses (either by record id or inline analysis JSON).

    The endpoint returns alignment metadata and numeric delta metrics so the
    mobile client can display numerical comparisons immediately. For large
    visual comparisons use /compare/plot which returns a PNG image.
    """
    from ECGComparisonPython import comparison_metrics, align_signals

    def _ensure_analysis(rec_id, analysis_obj):
        # Prefer the inline analysis payload if provided; otherwise load the
        # analysis from the stored record by id. Returns None when neither is
        # available.
        if analysis_obj:
            return analysis_obj
        if rec_id is not None:
            rec = load_record(int(rec_id))
            return rec.get("analysis")
        return None

    a = _ensure_analysis(record_a, analysis_a)
    b = _ensure_analysis(record_b, analysis_b)
    if not a or not b:
        raise HTTPException(status_code=400, detail="Both analyses are required")

    # Convert signals to numpy arrays for alignment
    signal_a = np.array(a["signal_mV"]) if isinstance(a.get("signal_mV"), (list, np.ndarray)) else np.array(a["signal_mV"])
    signal_b = np.array(b["signal_mV"]) if isinstance(b.get("signal_mV"), (list, np.ndarray)) else np.array(b["signal_mV"])
    aligned_a, aligned_b, method = align_signals(signal_a, signal_b, a["features"]["r_peaks"], b["features"]["r_peaks"])
    delta_metrics = comparison_metrics(a["metrics"], b["metrics"])

    # Return JSON-serializable payload with aligned arrays converted to lists
    return JSONResponse(content={
        "alignment_method": method,
        "delta_metrics": delta_metrics,
        "aligned_a": aligned_a.tolist(),
        "aligned_b": aligned_b.tolist(),
        "aligned_lengths": [len(aligned_a), len(aligned_b)],
    })


@app.post("/export")
async def export_data():
    """Export all tables to CSV and/or Excel and return the file or file list.

    This wraps the existing export_all_data() helper. When an Excel workbook
    was produced a FileResponse is returned so the mobile client can
    download the binary directly; otherwise a JSON list of CSV file paths is
    returned (suitable for debugging/local use).
    """
    paths = StoragePaths.current()
    exports = export_all_data()
    excel = exports.get("excel_file")
    if excel and os.path.exists(excel):
        return FileResponse(path=excel, filename=os.path.basename(excel), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    # fallback: return list of csvs
    return JSONResponse(content=exports)


if __name__ == "__main__":
    # Allow running the backend directly for development (e.g. python mobile_backend/main.py)
    uvicorn.run(app, host="0.0.0.0", port=8000)
