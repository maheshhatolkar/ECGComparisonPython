from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import io
import os
from typing import Optional

# Import analysis functions from the existing module
from ECGComparisonPython import build_analysis, compute_hash, StoragePaths, export_all_data, load_records, load_record, save_record
from PIL import Image
import numpy as np
import tempfile

app = FastAPI(title="ECGComparisonMobileBackend")

# Allow local development from mobile app served on different origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze")
async def analyze_image(pixels_per_mm: float = Form(...), prominence: float = Form(0.5), file: UploadFile = File(...)):
    """Accepts an uploaded image and returns the analysis JSON."""
    contents = await file.read()
    try:
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")
    analysis = build_analysis(image, pixels_per_mm, prominence)
    # Convert any numpy arrays to lists for JSON serialization
    def _normalize(obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: _normalize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_normalize(v) for v in obj]
        return obj

    return JSONResponse(content=_normalize(analysis))


@app.get("/records")
async def get_records():
    df = load_records()
    return JSONResponse(content=df.to_dict(orient="records"))


@app.get("/record/{record_id}")
async def get_single_record(record_id: int):
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
    """Accept analysis metadata and image, run analysis if not provided, and save record."""
    contents = await file.read()
    try:
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")
    analysis = build_analysis(image, pixels_per_mm, prominence)
    # metadata expected as JSON string
    try:
        meta = json.loads(metadata)
    except Exception:
        meta = {"note": metadata}

    # Save record using existing save_record helper which returns record id
    record_id = save_record(meta, contents, os.path.splitext(file.filename)[1], analysis)
    return JSONResponse(content={"record_id": record_id})


@app.post("/compare")
async def api_compare(record_a: Optional[int] = Form(None), record_b: Optional[int] = Form(None), analysis_a: Optional[dict] = Form(None), analysis_b: Optional[dict] = Form(None)):
    """Compare two analyses or two saved record ids and return comparison metrics."""
    from ECGComparisonPython import comparison_metrics, align_signals

    def _ensure_analysis(rec_id, analysis_obj):
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

    signal_a = np.array(a["signal_mV"]) if isinstance(a.get("signal_mV"), (list, np.ndarray)) else np.array(a["signal_mV"])
    signal_b = np.array(b["signal_mV"]) if isinstance(b.get("signal_mV"), (list, np.ndarray)) else np.array(b["signal_mV"])
    aligned_a, aligned_b, method = align_signals(signal_a, signal_b, a["features"]["r_peaks"], b["features"]["r_peaks"])
    delta_metrics = comparison_metrics(a["metrics"], b["metrics"])

    return JSONResponse(content={
        "alignment_method": method,
        "delta_metrics": delta_metrics,
        "aligned_a": aligned_a.tolist(),
        "aligned_b": aligned_b.tolist(),
    })


@app.post("/export")
async def export_data():
    paths = StoragePaths.current()
    exports = export_all_data()
    excel = exports.get("excel_file")
    if excel and os.path.exists(excel):
        return FileResponse(path=excel, filename=os.path.basename(excel), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    # fallback: return list of csvs
    return JSONResponse(content=exports)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
