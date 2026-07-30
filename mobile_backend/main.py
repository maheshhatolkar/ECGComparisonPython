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

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Header, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import io
import os
import time
import hmac
import hashlib
import base64

# Standard library JSON handling
import json

# Re-use core application helpers implemented in the main module. These
# functions perform analysis, interact with the SQLite DB, and produce
# structures compatible with our UI code.
from db import compute_hash, StoragePaths, load_records, load_record, save_record, get_setting, set_setting
from analyzer import build_analysis, comparison_metrics, align_signals
from auth import get_user_by_username, verify_password
from data_export import export_all_data

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
    allow_origin_regex=".*",
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


async def _extract_image_bytes(file: UploadFile | None = None, image_base64: str | None = None) -> bytes:
    if file and file.filename:
        return await file.read()
    if image_base64:
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]
        return base64.b64decode(image_base64)
    if file:
        return await file.read()
    raise HTTPException(status_code=400, detail="No image file or image_base64 provided")


@app.post("/analyze")
async def analyze_image(request: Request):
    """Analyze an uploaded ECG image (file upload, form image_base64, or JSON) and return the analysis dictionary."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        img_b64 = body.get("image_base64")
        pixels_per_mm = float(body.get("pixels_per_mm", 20.0))
        prominence = float(body.get("prominence", 0.5))
        contents = await _extract_image_bytes(None, img_b64)
    else:
        form = await request.form()
        file_obj = form.get("file")
        img_b64 = form.get("image_base64")
        pixels_per_mm = float(form.get("pixels_per_mm", 20.0))
        prominence = float(form.get("prominence", 0.5))
        if file_obj and hasattr(file_obj, "read"):
            contents = await file_obj.read()
        elif img_b64:
            contents = await _extract_image_bytes(None, str(img_b64))
        else:
            raise HTTPException(status_code=400, detail="No image file or image_base64 provided")

    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")
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


@app.get("/settings/{key}")
async def api_get_setting(key: str, default: str = ""):
    val = get_setting(key, default)
    return JSONResponse(content={"key": key, "value": val if val is not None else default})


@app.post("/settings/{key}")
async def api_set_setting(key: str, value: str = Form("")):
    set_setting(key, value)
    return JSONResponse(content={"key": key, "value": value})


@app.post("/login")
async def login(username: str = Form(""), password: str = Form("")):
    """Authenticate a user using the existing local user store.

    This endpoint reuses the get_user_by_username() and verify_password()
    helpers from ECGComparisonPython.py. On success it returns a minimal
    user profile and a short-lived signed token the mobile client can use to
    authenticate administrative requests.
    """
    if not username or not password:
        raise HTTPException(status_code=401, detail="Username and password are required")
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
    df = df.replace({np.nan: None})
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
async def api_save_record(request: Request):
    """Save an ECG record: accept image + metadata, run analysis, and store.

    The file field is truly optional – when omitted the server stores
    a minimal placeholder image and an empty analysis skeleton so the
    record metadata (patient_id, ecg_datetime, root_cause, …) is never
    lost due to an image processing error.
    """
    form = await request.form()
    metadata = form.get("metadata", "{}")
    pixels_per_mm = float(form.get("pixels_per_mm", 20.0))
    prominence = float(form.get("prominence", 0.5))
    file_obj = form.get("file")

    if file_obj and hasattr(file_obj, "read"):
        contents = await file_obj.read()
        ext = os.path.splitext(getattr(file_obj, "filename", "") or "")[1] or ".png"
    else:
        # No file uploaded — create a small placeholder image
        img_temp = Image.new("RGB", (800, 300), color="white")
        buf = io.BytesIO()
        img_temp.save(buf, format="PNG")
        contents = buf.getvalue()
        ext = ".png"

    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")
    try:
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")

    # Run analysis but don't fail the save if the image lacks a valid waveform
    try:
        analysis = build_analysis(image, pixels_per_mm, prominence)
    except Exception:
        # Return an empty analysis skeleton so the record can still be saved
        analysis = {
            "signal_mV": [],
            "time_ms": [],
            "features": {"r_peaks": [], "p_peaks": [], "t_peaks": [], "q_valleys": [], "s_valleys": []},
            "metrics": {"heart_rate_bpm": None, "pr_interval_ms": None, "qrs_duration_ms": None, "qt_interval_ms": None},
        }

    try:
        meta = json.loads(metadata)
    except Exception:
        meta = {"note": metadata}

    record_id = save_record(meta, contents, ext, analysis)
    return JSONResponse(content={"record_id": record_id})


@app.post("/compare")
async def api_compare(record_a: int | None = Form(None), record_b: int | None = Form(None), analysis_a: str | None = Form(None), analysis_b: str | None = Form(None)):
    """Compare two ECG analyses (either by record id or inline analysis JSON).

    The endpoint returns alignment metadata and numeric delta metrics so the
    mobile client can display numerical comparisons immediately. For large
    visual comparisons use /compare/plot which returns a PNG image.
    """

    def _ensure_analysis(rec_id, analysis_str):
        # Prefer the inline analysis payload if provided; otherwise load the
        # analysis from the stored record by id. Returns None when neither is
        # available.
        if analysis_str:
            try:
                return json.loads(analysis_str)
            except Exception:
                pass
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




@app.post("/analysis/plot")
async def analysis_plot(analysis: dict):
    """Generate a base64 encoded PNG plot of a single ECG analysis.

    Accepts an analysis dictionary (containing signal_mV, time_ms, and features)
    and uses matplotlib to render a visualization. Returns a JSON object with
    the base64 encoded image string.
    """
    from plotting import render_signal_plot
    import matplotlib.pyplot as plt
    try:
        signal = np.array(analysis["signal_mV"])
        time_ms = np.array(analysis["time_ms"])
        fig = render_signal_plot(signal, time_ms, analysis.get("features"))
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png')
        plt.close(fig)
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode()
        return JSONResponse(content={"plot_base64": img_base64})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/compare/plot")
async def compare_plot(aligned: dict):
    """Generate a base64 encoded PNG plot comparing two aligned ECG signals.

    Accepts a dictionary containing aligned_a and aligned_b signal arrays
    and uses matplotlib to render an overlay visualization. Returns a JSON
    object with the base64 encoded image string.
    """
    from plotting import render_comparison_plot
    import matplotlib.pyplot as plt
    try:
        signal_a = np.array(aligned["aligned_a"])
        signal_b = np.array(aligned["aligned_b"])
        fig = render_comparison_plot(signal_a, signal_b)
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png')
        plt.close(fig)
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode()
        return JSONResponse(content={"plot_base64": img_base64})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/tables")
async def get_tables():
    """Retrieve a list of all table names in the local SQLite database.

    Useful for administrative interfaces that need to dynamically discover
    available tables for data export or inspection.
    """
    import sqlite3
    paths = StoragePaths.current()
    with sqlite3.connect(paths.db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return JSONResponse(content=[r[0] for r in rows])

@app.get("/table/{t}")
async def get_table_data(t: str):
    """Retrieve all rows from a specific database table.

    Reads the table into a pandas DataFrame and returns it as a JSON list
    of record dictionaries, replacing NaNs with nulls for JSON compatibility.
    """
    import sqlite3
    import pandas as pd
    paths = StoragePaths.current()
    with sqlite3.connect(paths.db_path) as conn:
        try:
            df = pd.read_sql_query(f"SELECT * FROM [{t}]", conn)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    df = df.replace({np.nan: None})
    return JSONResponse(content=df.to_dict(orient="records"))


@app.on_event("startup")
def startup_event():
    """Initialize database schemas on backend startup."""
    from db import init_db
    init_db()


@app.get("/settings/{key}")
async def api_get_setting(key: str, default: str = ""):
    """Retrieve a configuration setting by key."""
    from db import get_setting
    return {"value": get_setting(key, default)}


@app.post("/settings/{key}")
async def api_set_setting(key: str, value: str = Form(...)):
    """Update a configuration setting."""
    from db import set_setting
    set_setting(key, value)
    return {"status": "success"}


@app.get("/users")
async def api_list_users():
    """List all registered users."""
    from auth import list_users
    df = list_users()
    df = df.replace({np.nan: None})
    return JSONResponse(content=df.to_dict(orient="records"))


@app.post("/users")
async def api_create_user(
    username: str = Form(...),
    display_name: str = Form(None),
    role: str = Form(...),
    password: str = Form(...),
    enabled: bool = Form(True)
):
    """Create a new user account."""
    from auth import create_user
    try:
        create_user(username, display_name, role, password, enabled)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/users/{user_id}")
async def api_update_user(
    user_id: int,
    display_name: str = Form(None),
    role: str = Form(...),
    enabled: bool = Form(True)
):
    """Update an existing user's details."""
    from auth import update_user
    try:
        update_user(user_id, display_name, role, enabled)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/users/{user_id}/reset_password")
async def api_reset_password(user_id: int, password: str = Form(...)):
    """Reset a user's password."""
    from auth import reset_password
    try:
        reset_password(user_id, password)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/audit_logs")
async def api_list_audit_logs(limit: int = 200):
    """Retrieve system audit logs."""
    from auth import list_audit_logs
    df = list_audit_logs(limit=limit)
    df = df.replace({np.nan: None})
    return JSONResponse(content=df.to_dict(orient="records"))


@app.post("/audit_log")
async def api_log_audit(
    event_type: str = Form(...),
    outcome: str = Form(...),
    user_json: str = Form(None),
    details: str = Form(None)
):
    """Log a security or system action event."""
    from auth import log_audit
    user = json.loads(user_json) if user_json else None
    log_audit(event_type, outcome, user, details)
    return {"status": "success"}


@app.delete("/record/{record_id}")
async def api_delete_record(record_id: int):
    """Delete a record by ID."""
    from db import delete_record
    success = delete_record(record_id)
    if not success:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"status": "success"}


@app.post("/detect_grid_spacing")
async def api_detect_grid_spacing(file: UploadFile = File(...)):
    """Analyze uploaded image and detect pixels per 1 mm grid spacing."""
    from analyzer import preprocess_image, detect_grid_spacing
    contents = await file.read()
    try:
        image = Image.open(io.BytesIO(contents)).convert("RGB")
        prep = preprocess_image(image)
        spacing = detect_grid_spacing(prep["enhanced"])
        return {"grid_spacing": spacing}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/compare/delta_plot")
async def compare_delta_plot(payload: dict):
    """Generate a base64 encoded PNG plot of differences between two signals."""
    from plotting import render_delta_plot
    import matplotlib.pyplot as plt
    try:
        delta = np.array(payload["delta"])
        fig = render_delta_plot(delta)
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png')
        plt.close(fig)
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode()
        return JSONResponse(content={"plot_base64": img_base64})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    # Allow running the backend directly for development (e.g. python mobile_backend/main.py)
    uvicorn.run(app, host="0.0.0.0", port=8000)
