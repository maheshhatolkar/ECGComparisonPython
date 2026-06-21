import os, sys, re

with open('ECGComparisonPython.py', 'r', encoding='utf-8') as f:
    orig = f.read()

app_py = '''import os
import io
import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import numpy as np
import streamlit as st
import fitz
from PIL import Image

from db import init_db, get_setting, set_setting, load_records, load_record, save_record, delete_record, compute_hash, StoragePaths
from analyzer import preprocess_image, detect_grid_spacing, build_analysis, align_signals, comparison_metrics, metrics_table, analysis_to_exports
from auth import log_audit, authenticate_user, is_user_management_enabled, get_session_timeout_minutes, user_has_role, require_roles, enforce_session_timeout, create_user, update_user, reset_password, list_audit_logs, list_users
from plotting import render_signal_plot, render_comparison_plot, render_delta_plot
import data_export
import matplotlib.pyplot as plt

def mask_patient_id(value: str | None) -> str | None:
    if not value:
        return value
    return "***"

def open_pdf_first_page(pdf_bytes: bytes) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=200)
    img_data = pix.tobytes("png")
    return Image.open(io.BytesIO(img_data)).convert("RGB")

def load_image_from_upload(uploaded_file):
    if uploaded_file is None:
        return None, None, None
    data = uploaded_file.getvalue()
    if len(data) > 10 * 1024 * 1024:
        st.error("File is too large (max 10MB).")
        st.stop()
    filename = uploaded_file.name.lower()
    if filename.endswith(".pdf"):
        image = open_pdf_first_page(data)
        ext = ".png"
    else:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        ext = os.path.splitext(filename)[1] or ".png"
    return image, data, ext

'''

main_match = re.search(r'def main\(\):.*', orig, re.DOTALL)
main_str = main_match.group(0)
main_str = main_str.replace('datetime.utcnow()', 'datetime.now(timezone.utc)')

# Fix memory leaks in Streamlit plots (M9)
main_str = main_str.replace('st.pyplot(render_signal_plot(signal, time_ms, analysis["features"]))',
                            '''fig = render_signal_plot(signal, time_ms, analysis["features"])
                st.pyplot(fig)
                plt.close(fig)''')

main_str = main_str.replace('st.pyplot(render_comparison_plot(aligned_a, aligned_b))',
                            '''fig = render_comparison_plot(aligned_a, aligned_b)
            st.pyplot(fig)
            plt.close(fig)''')

main_str = main_str.replace('st.pyplot(render_delta_plot(delta))',
                            '''fig = render_delta_plot(delta)
            st.pyplot(fig)
            plt.close(fig)''')

app_py += main_str

with open('ECGComparisonPython.py', 'w', encoding='utf-8') as f:
    f.write(app_py)

print("ECGComparisonPython.py updated successfully.")
