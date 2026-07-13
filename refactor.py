import os, sys, re

with open('ECGComparisonPython.py', 'r', encoding='utf-8') as f:
    orig = f.read()

# ----------------- db.py -----------------
db_py = '''import os
import json
import sqlite3
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
import pandas as pd
import functools

DB_SCHEMA_VERSION = 2
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGE_DIR = os.path.join(DATA_DIR, "images")
DB_PATH = os.path.join(DATA_DIR, "ecg.db")

@dataclass(frozen=True)
class StoragePaths:
    base_dir: str
    data_dir: str
    image_dir: str
    db_path: str

    @classmethod
    def current(cls) -> "StoragePaths":
        return cls(base_dir=BASE_DIR, data_dir=DATA_DIR, image_dir=IMAGE_DIR, db_path=DB_PATH)

'''

db_match = re.search(r'class ECGDatabase:.*?(?=class ECGAnalyzer:)', orig, re.DOTALL)
ecg_db_str = db_match.group(0)

ecg_db_str = ecg_db_str.replace('datetime.utcnow()', 'datetime.now(timezone.utc)')

delete_orig = re.search(r'    def delete_record\(self, record_id: int\) -> bool:.*?    class', ecg_db_str, re.DOTALL)
if delete_orig:
    delete_new = '''    def delete_record(self, record_id: int) -> bool:
        self.ensure_storage()
        image_filename = None
        with sqlite3.connect(self._paths.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            row = conn.execute("SELECT image_filename FROM ecg_records WHERE id = ?", (record_id,)).fetchone()
            if not row:
                return False
            image_filename = row[0]
            conn.execute("DELETE FROM ecg_comparisons WHERE record_a_id = ? OR record_b_id = ?", (record_id, record_id))
            conn.execute("DELETE FROM ecg_records WHERE id = ?", (record_id,))
            count_row = conn.execute("SELECT COUNT(*) FROM ecg_records WHERE image_filename = ?", (image_filename,)).fetchone()
            remaining = int(count_row[0]) if count_row else 0

        if image_filename and remaining == 0:
            path = os.path.join(self._paths.image_dir, image_filename)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        return True

    class'''
    ecg_db_str = ecg_db_str.replace(delete_orig.group(0), delete_new)

if ecg_db_str.endswith('    class'):
    ecg_db_str = ecg_db_str[:-9]

db_py += ecg_db_str + '''
@functools.lru_cache(maxsize=1)
def get_db() -> ECGDatabase:
    return ECGDatabase(StoragePaths.current())

def compute_hash(data: bytes) -> str:
    return get_db().compute_hash(data)

def save_image_bytes(image_bytes: bytes, ext: str) -> str:
    return get_db().save_image_bytes(image_bytes, ext)

def load_records() -> pd.DataFrame:
    return get_db().load_records()

def load_record(record_id: int) -> dict:
    return get_db().load_record(record_id)

def save_record(metadata: dict, image_bytes: bytes, ext: str, analysis: dict) -> int:
    return get_db().save_record(metadata, image_bytes, ext, analysis)

def delete_record(record_id: int) -> bool:
    return get_db().delete_record(record_id)

def init_db():
    get_db().init_db()

def get_setting(key: str, default: str | None = None) -> str | None:
    return get_db().get_setting(key, default)

def set_setting(key: str, value: str) -> None:
    get_db().set_setting(key, value)
'''
with open('db.py', 'w', encoding='utf-8') as f:
    f.write(db_py)


# ----------------- analyzer.py -----------------
analyzer_py = '''import numpy as np
import pandas as pd
import cv2
from PIL import Image
from scipy.signal import find_peaks, savgol_filter
from scipy.signal import correlate
import functools

'''
analyzer_match = re.search(r'class ECGAnalyzer:.*?(?=def compute_hash\()', orig, re.DOTALL)
ecg_analyzer_str = analyzer_match.group(0)
ecg_analyzer_str = re.sub(r'def _analyzer\(\).*', '', ecg_analyzer_str, flags=re.DOTALL)

analyzer_py += ecg_analyzer_str + '''
@functools.lru_cache(maxsize=1)
def get_analyzer() -> ECGAnalyzer:
    return ECGAnalyzer()

@functools.lru_cache(maxsize=1)
def get_aligner() -> ECGAligner:
    return ECGAligner()

@functools.lru_cache(maxsize=1)
def get_exporter() -> ECGExporter:
    return ECGExporter()

def preprocess_image(image: Image.Image) -> dict:
    return get_analyzer().preprocess_image(image)

def detect_grid_spacing(enhanced_gray: np.ndarray) -> float | None:
    return get_analyzer().detect_grid_spacing(enhanced_gray)

def digitize_waveform(enhanced_gray: np.ndarray) -> np.ndarray:
    return get_analyzer().digitize_waveform(enhanced_gray)

def extract_features(signal: np.ndarray, ms_per_pixel: float, r_peaks: np.ndarray) -> dict:
    return get_analyzer().extract_features(signal, ms_per_pixel, r_peaks)

def compute_metrics(features: dict, ms_per_pixel: float) -> dict:
    return get_analyzer().compute_metrics(features, ms_per_pixel)

def waveform_to_signal(y_pixels: np.ndarray, mV_per_pixel: float) -> np.ndarray:
    return get_analyzer().waveform_to_signal(y_pixels, mV_per_pixel)

def detect_r_peaks(signal: np.ndarray, ms_per_pixel: float, prominence_factor: float = 0.5) -> np.ndarray:
    return get_analyzer().detect_r_peaks(signal, ms_per_pixel, prominence_factor)

def build_analysis(image: Image.Image, pixels_per_mm: float, prominence_factor: float = 0.5) -> dict:
    return get_analyzer().build_analysis(image, pixels_per_mm, prominence_factor)

def metrics_table(metrics: dict) -> pd.DataFrame:
    return get_exporter().metrics_table(metrics)

def align_signals(signal_a: np.ndarray, signal_b: np.ndarray, r_a: list, r_b: list) -> tuple:
    return get_aligner().align_signals(signal_a, signal_b, r_a, r_b)

def analysis_to_exports(analysis: dict) -> str:
    return get_exporter().analysis_to_exports(analysis)

def comparison_metrics(metrics_a: dict, metrics_b: dict) -> dict:
    keys = ["heart_rate_bpm", "pr_interval_ms", "qrs_duration_ms", "qt_interval_ms"]
    delta = {}
    for key in keys:
        a_val = metrics_a.get(key)
        b_val = metrics_b.get(key)
        if a_val is None or b_val is None:
            delta[key] = None
        else:
            delta[key] = float(b_val - a_val)
    return delta
'''
with open('analyzer.py', 'w', encoding='utf-8') as f:
    f.write(analyzer_py)


# ----------------- auth.py -----------------
auth_py = '''import sqlite3
import hashlib
import secrets
import pandas as pd
import streamlit as st
from datetime import datetime, timezone
from db import StoragePaths, get_setting

'''
auth_funcs = ["hash_password", "verify_password", "get_user_by_username", "list_users", "create_user", "update_user", "reset_password", "log_audit", "list_audit_logs", "authenticate_user", "is_user_management_enabled", "get_session_timeout_minutes", "user_has_role", "require_roles", "enforce_session_timeout"]

for func in auth_funcs:
    match = re.search(r'def ' + func + r'\(.*?(?=\ndef |$)', orig, re.DOTALL)
    if match:
        body = match.group(0)
        body = body.replace('datetime.utcnow()', 'datetime.now(timezone.utc)')
        auth_py += body + "\\n\\n"

with open('auth.py', 'w', encoding='utf-8') as f:
    f.write(auth_py)


# ----------------- plotting.py -----------------
plotting_py = '''import numpy as np
import matplotlib
import matplotlib.pyplot as plt

'''
plot_funcs = ["render_signal_plot", "render_comparison_plot", "render_delta_plot"]

for func in plot_funcs:
    match = re.search(r'def ' + func + r'\(.*?(?=\ndef |$)', orig, re.DOTALL)
    if match:
        body = match.group(0)
        # remove internal import matplotlib.pyplot as plt
        body = re.sub(r'\\s*import matplotlib.pyplot as plt\\n', '', body)
        plotting_py += body + "\\n\\n"

with open('plotting.py', 'w', encoding='utf-8') as f:
    f.write(plotting_py)

print("Files generated successfully.")
