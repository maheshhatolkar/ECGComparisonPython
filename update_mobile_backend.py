import os

with open('mobile_backend/main.py', 'r', encoding='utf-8') as f:
    orig = f.read()

# Fix CORS
orig = orig.replace('allow_credentials=True,', 'allow_credentials=False,')

# Fix imports
orig = orig.replace('''from ECGComparisonPython import (
    build_analysis,
    compute_hash,
    StoragePaths,
    export_all_data,
    load_records,
    load_record,
    save_record,
    get_user_by_username,
    verify_password,
)''', '''from db import compute_hash, StoragePaths, load_records, load_record, save_record
from analyzer import build_analysis, comparison_metrics, align_signals
from auth import get_user_by_username, verify_password
from data_export import export_all_data''')

orig = orig.replace('''    from ECGComparisonPython import comparison_metrics, align_signals\n''', '')

# Fix file size upload limit
orig = orig.replace('''    contents = await file.read()
    try:
        # Load the image from uploaded bytes and ensure RGB mode
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")''', '''    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")
    try:
        # Load the image from uploaded bytes and ensure RGB mode
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")''')

orig = orig.replace('''    contents = await file.read()
    try:
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")''', '''    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")
    try:
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")''')

# Add missing endpoints
endpoints = '''

@app.post("/analysis/plot")
async def analysis_plot(analysis: dict):
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
    import sqlite3
    paths = StoragePaths.current()
    with sqlite3.connect(paths.db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return JSONResponse(content=[r[0] for r in rows])

@app.get("/table/{t}")
async def get_table_data(t: str):
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

'''

orig = orig.replace('''if __name__ == "__main__":''', endpoints + '''if __name__ == "__main__":''')

with open('mobile_backend/main.py', 'w', encoding='utf-8') as f:
    f.write(orig)

print("Updated mobile_backend/main.py")
