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
    return JSONResponse(content=analysis)


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
