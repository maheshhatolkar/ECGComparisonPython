import os
import io
import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import numpy as np
import streamlit as st
import fitz
from PIL import Image

import requests
import base64
import hashlib

import db
import auth

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")

def init_db(*args, **kwargs):
    try:
        db.init_db()
    except Exception:
        pass
    try:
        requests.get(f"{BACKEND_URL}/records", timeout=1)
    except Exception:
        pass

def get_setting(key: str, default: str = "") -> str:
    try:
        res = requests.get(f"{BACKEND_URL}/settings/{key}", params={"default": default}, timeout=1)
        if res.status_code == 200:
            val = res.json().get("value")
            if val is not None:
                return str(val)
    except Exception:
        pass
    try:
        val = db.get_setting(key, default)
        return str(val) if val is not None else default
    except Exception:
        return default

def set_setting(key: str, value: str):
    try:
        requests.post(f"{BACKEND_URL}/settings/{key}", data={"value": value}, timeout=1)
    except Exception:
        pass
    try:
        db.set_setting(key, value)
    except Exception:
        pass

def load_records() -> pd.DataFrame:
    try:
        res = requests.get(f"{BACKEND_URL}/records")
        if res.status_code == 200:
            return pd.DataFrame(res.json())
    except Exception:
        pass
    return pd.DataFrame()

def load_record(record_id: int) -> dict:
    try:
        res = requests.get(f"{BACKEND_URL}/record/{record_id}")
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return {}

def save_record(metadata: dict, image_bytes: bytes, ext: str, analysis: dict) -> int:
    try:
        files = {"file": ("ecg_image" + ext, image_bytes, "image/png" if ext == ".png" else "image/jpeg")}
        data = {
            "metadata": json.dumps(metadata),
            "pixels_per_mm": analysis.get("pixels_per_mm", 20.0),
            "prominence": analysis.get("prominence", 0.5)
        }
        res = requests.post(f"{BACKEND_URL}/save_record", files=files, data=data)
        if res.status_code == 200:
            return res.json().get("record_id")
    except Exception as e:
        st.error(f"Save record API failed: {e}")
    return 0

def delete_record(record_id: int) -> bool:
    try:
        res = requests.delete(f"{BACKEND_URL}/record/{record_id}")
        return res.status_code == 200
    except Exception:
        return False

def compute_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()

def detect_grid_spacing_api(image_bytes: bytes) -> float | None:
    try:
        files = {"file": ("ecg_image.png", image_bytes, "image/png")}
        res = requests.post(f"{BACKEND_URL}/detect_grid_spacing", files=files)
        if res.status_code == 200:
            return res.json().get("grid_spacing")
    except Exception:
        pass
    return None

def build_analysis_api(image_bytes: bytes, pixels_per_mm: float, prominence: float) -> dict:
    try:
        files = {"file": ("ecg_image.png", image_bytes, "image/png")}
        data = {"pixels_per_mm": pixels_per_mm, "prominence": prominence}
        res = requests.post(f"{BACKEND_URL}/analyze", files=files, data=data)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        st.error(f"Analysis API failed: {e}")
    return {}

def align_and_compare_api(record_a=None, record_b=None, analysis_a=None, analysis_b=None) -> dict:
    try:
        data = {}
        if record_a is not None:
            data["record_a"] = record_a
        if record_b is not None:
            data["record_b"] = record_b
        if analysis_a is not None:
            data["analysis_a"] = json.dumps(analysis_a)
        if analysis_b is not None:
            data["analysis_b"] = json.dumps(analysis_b)
        
        res = requests.post(f"{BACKEND_URL}/compare", data=data)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        st.error(f"Comparison API failed: {e}")
    return {}

def metrics_table(metrics: dict | None) -> list:
    if not isinstance(metrics, dict):
        metrics = {}
    return [
        {"Parameter": "Heart Rate (bpm)", "Value": metrics.get("heart_rate_bpm")},
        {"Parameter": "PR Interval (ms)", "Value": metrics.get("pr_interval_ms")},
        {"Parameter": "QRS Duration (ms)", "Value": metrics.get("qrs_duration_ms")},
        {"Parameter": "QT Interval (ms)", "Value": metrics.get("qt_interval_ms")},
    ]

def render_signal_plot_api(analysis: dict):
    try:
        res = requests.post(f"{BACKEND_URL}/analysis/plot", json=analysis)
        if res.status_code == 200:
            img_b64 = res.json().get("plot_base64")
            if img_b64:
                return base64.b64decode(img_b64)
    except Exception:
        pass
    return None

def render_comparison_plot_api(aligned_a: list, aligned_b: list):
    try:
        res = requests.post(f"{BACKEND_URL}/compare/plot", json={"aligned_a": aligned_a, "aligned_b": aligned_b})
        if res.status_code == 200:
            img_b64 = res.json().get("plot_base64")
            if img_b64:
                return base64.b64decode(img_b64)
    except Exception:
        pass
    return None

def render_delta_plot_api(delta: list):
    try:
        res = requests.post(f"{BACKEND_URL}/compare/delta_plot", json={"delta": delta})
        if res.status_code == 200:
            img_b64 = res.json().get("plot_base64")
            if img_b64:
                return base64.b64decode(img_b64)
    except Exception:
        pass
    return None

def authenticate_user(username, password) -> dict | None:
    try:
        res = requests.post(f"{BACKEND_URL}/login", data={"username": username, "password": password}, timeout=2)
        if res.status_code == 200:
            data = res.json()
            st.session_state["token"] = data.get("token", "")
            return data
    except Exception:
        pass
    try:
        return auth.authenticate_user(username, password)
    except Exception:
        return None

def is_user_management_enabled() -> bool:
    return get_setting("user_management_enabled", "true") == "true"

def get_session_timeout_minutes() -> int:
    try:
        return int(get_setting("session_timeout_minutes", "30"))
    except Exception:
        return 30

def user_has_role(allowed_roles: list) -> bool:
    if not is_user_management_enabled():
        return True
    if not st.session_state.get("authenticated"):
        return False
    return st.session_state.get("role") in allowed_roles

def require_roles(allowed_roles: list):
    if not is_user_management_enabled():
        return
    if not st.session_state.get("authenticated"):
        st.error("Authentication required.")
        st.stop()
    if st.session_state.get("role") not in allowed_roles:
        st.error("Access denied. Insufficient permissions.")
        st.stop()

def enforce_session_timeout():
    if not is_user_management_enabled() or not st.session_state.get("authenticated"):
        return
    last_act_str = st.session_state.get("last_activity")
    if last_act_str:
        try:
            last_act = datetime.fromisoformat(last_act_str)
            timeout = get_session_timeout_minutes()
            delta_mins = (datetime.now(timezone.utc) - last_act).total_seconds() / 60.0
            if delta_mins > timeout:
                st.session_state.clear()
                st.warning("Session timed out due to inactivity.")
                st.stop()
        except Exception:
            pass
    st.session_state["last_activity"] = datetime.now(timezone.utc).isoformat()

def create_user(username, display_name, role, password, enabled):
    try:
        requests.post(f"{BACKEND_URL}/users", data={
            "username": username,
            "display_name": display_name,
            "role": role,
            "password": password,
            "enabled": enabled
        }, timeout=2)
    except Exception:
        pass

def update_user(user_id, display_name, role, enabled):
    try:
        requests.put(f"{BACKEND_URL}/users/{user_id}", data={
            "display_name": display_name,
            "role": role,
            "enabled": enabled
        }, timeout=2)
    except Exception:
        pass

def reset_password(user_id, password):
    try:
        requests.post(f"{BACKEND_URL}/users/{user_id}/reset_password", data={"password": password}, timeout=2)
    except Exception:
        pass

def list_users() -> pd.DataFrame:
    try:
        res = requests.get(f"{BACKEND_URL}/users", timeout=2)
        if res.status_code == 200:
            return pd.DataFrame(res.json())
    except Exception:
        pass
    return pd.DataFrame()

def list_audit_logs(limit: int = 200) -> pd.DataFrame:
    try:
        res = requests.get(f"{BACKEND_URL}/audit_logs", params={"limit": limit}, timeout=2)
        if res.status_code == 200:
            return pd.DataFrame(res.json())
    except Exception:
        pass
    return pd.DataFrame()

def log_audit(event_type: str, outcome: str, user: dict | None = None, details: str | None = None):
    try:
        requests.post(f"{BACKEND_URL}/audit_log", data={
            "event_type": event_type,
            "outcome": outcome,
            "user_json": json.dumps(user) if user else "",
            "details": details or ""
        }, timeout=2)
    except Exception:
        pass

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
    try:
        if filename.endswith(".pdf"):
            image = open_pdf_first_page(data)
            ext = ".png"
        else:
            image = Image.open(io.BytesIO(data)).convert("RGB")
            ext = os.path.splitext(filename)[1] or ".png"
        return image, data, ext
    except Exception as e:
        st.error(f"Unable to read file: {e}. Please upload a valid PNG, JPG, or PDF file.")
        return None, None, None

@st.cache_resource
def _initialize_db():
    init_db()

def main():
    """Streamlit GUI entry point."""
    # Initialize UI and persistent storage.
    st.set_page_config(page_title="ECG Graph Extraction", layout="wide")
    _initialize_db()
    enforce_session_timeout()

    st.title("ECG Graph Extraction and Analysis")
    st.caption("Upload ECG images, digitize waveforms, extract features, compare, and store results.")

    with st.sidebar:
        st.header("User Access")
        if is_user_management_enabled():
            # Render login/logout flow depending on session state.
            if st.session_state.get("authenticated"):
                st.write(f"Signed in as: {st.session_state.get('username')}")
                st.write(f"Role: {st.session_state.get('role')}")
                if st.button("Logout"):
                    user = {
                        "id": st.session_state.get("user_id"),
                        "username": st.session_state.get("username"),
                    }
                    log_audit("logout", "success", user)
                    st.session_state.clear()
                    st.rerun()
            else:
                with st.form("login_form"):
                    username = st.text_input("Username")
                    password = st.text_input("Password", type="password")
                    submitted = st.form_submit_button("Login")
                    if submitted:
                        user = authenticate_user(username, password)
                        if user:
                            st.session_state["authenticated"] = True
                            st.session_state["user_id"] = user["id"]
                            st.session_state["username"] = user["username"]
                            st.session_state["role"] = user["role"]
                            st.session_state["last_activity"] = datetime.now(timezone.utc).isoformat()
                            log_audit("login", "success", user)
                            st.rerun()
                        log_audit("login", "failure", {"username": username})
                        st.error("Invalid credentials or user disabled.")
        else:
            st.info("User management is disabled.")
            if st.button("Enable User Management"):
                set_setting("user_management_enabled", "true")
                st.rerun()

    default_admin_created = get_setting("default_admin_created") == "true"
    if default_admin_created and is_user_management_enabled():
        # Surface the default admin reminder in case password needs reset.
        st.warning("Default admin account exists (username: admin, password: admin). Please log in.")

    if is_user_management_enabled() and not st.session_state.get("authenticated"):
        st.info("🔒 **User Management is Enabled.** Please log in below or from the sidebar to access the application.")
        col1, col2 = st.columns([1, 2])
        with col1:
            with st.form("main_login_form"):
                st.subheader("Login to ECG Application")
                username = st.text_input("Username", key="main_user")
                password = st.text_input("Password", type="password", key="main_pass")
                submitted = st.form_submit_button("Login", type="primary")
                if submitted:
                    user = authenticate_user(username, password)
                    if user:
                        st.session_state["authenticated"] = True
                        st.session_state["user_id"] = user["id"]
                        st.session_state["username"] = user["username"]
                        st.session_state["role"] = user["role"]
                        st.session_state["last_activity"] = datetime.now(timezone.utc).isoformat()
                        log_audit("login", "success", user)
                        st.rerun()
                    log_audit("login", "failure", {"username": username})
                    st.error("Invalid credentials or user disabled.")
        return

    # Define the top-level tabs. Include Admin tab only when user management is enabled.
    base_tabs = ["Analyze", "Compare", "Records"]
    # Add an Admin-only Analysis tab (visible only to Administrator role)
    if user_has_role(["Administrator"]):
        base_tabs.append("Analysis")
    if is_user_management_enabled():
        base_tabs.append("Admin")

    tabs = st.tabs(base_tabs)
    # Map tabs to variables by position so the code works regardless of which
    # optional tabs were added above.
    tab_analyze = tabs[0]
    tab_compare = tabs[1]
    tab_records = tabs[2]
    tab_analysis = None
    tab_admin_obj = None
    next_idx = 3
    if "Analysis" in base_tabs:
        tab_analysis = tabs[next_idx]
        next_idx += 1
    if "Admin" in base_tabs:
        tab_admin_obj = tabs[next_idx]

    with tab_analyze:
        require_roles(["Administrator", "Clinician", "Researcher"])
        st.subheader("Analyze ECG Image")
        uploaded = st.file_uploader("Upload ECG image (PNG/JPG/PDF)", type=["png", "jpg", "jpeg", "pdf"])
        camera_photo = st.camera_input("Take a picture of ECG (PNG/JPG)")
        
        selected_upload = uploaded or camera_photo

        if selected_upload:
            # Load uploaded file and reset cached analysis on file change.
            image, image_bytes, ext = load_image_from_upload(selected_upload)
            if image is None:
                st.error("Unable to read the uploaded file.")
                st.stop()

            current_hash = compute_hash(image_bytes)
            if st.session_state.get("last_upload_hash") != current_hash:
                st.session_state["last_upload_hash"] = current_hash
                st.session_state.pop("analysis", None)
                st.session_state["image_bytes"] = image_bytes
                st.session_state["image_ext"] = ext

            st.image(image, caption="Original ECG", use_container_width=True)
            grid_spacing = detect_grid_spacing_api(image_bytes)
            st.write("Grid detection:")

            if grid_spacing:
                st.success(f"Detected grid spacing: {grid_spacing:.2f} px per 1 mm")
            else:
                st.warning("Automatic grid spacing failed. Please set pixels per mm manually.")

            manual_pixels_per_mm = st.slider("Pixels per mm", min_value=5.0, max_value=40.0, value=20.0, step=0.5)
            pixels_per_mm = grid_spacing if grid_spacing else manual_pixels_per_mm

            prominence_factor = st.slider("R-peak sensitivity", min_value=0.1, max_value=1.0, value=0.5, step=0.1)

            if st.button("Run Analysis"):
                # Run the full pipeline and display plots/metrics via API.
                analysis = build_analysis_api(image_bytes, pixels_per_mm, prominence_factor)
                st.session_state["analysis"] = analysis

            if "analysis" in st.session_state:
                # Render waveform plot, metrics, and export options.
                analysis = st.session_state["analysis"]
                plot_bytes = render_signal_plot_api(analysis)
                if plot_bytes:
                    st.image(plot_bytes, caption="ECG Waveform Analysis")
                st.dataframe(metrics_table(analysis["metrics"]))

                with st.form("save_record"):
                    st.write("Save to database")
                    patient_id = st.text_input("Patient ID")
                    ecg_datetime = st.text_input("ECG date/time")
                    root_cause = st.text_input("Possible root cause")
                    root_cause_time = st.text_input("Time of root cause")
                    submitted = st.form_submit_button("Save record")
                    if submitted:
                        # Respect privacy settings when storing identifiers.
                        allow_patient_storage = get_setting("allow_patient_data_storage", "false") == "true"
                        if not allow_patient_storage and patient_id:
                            patient_id = None
                            st.warning("Patient identifiers are not stored unless enabled by an Administrator.")
                        metadata = {
                            "patient_id": patient_id,
                            "ecg_datetime": ecg_datetime,
                            "root_cause": root_cause,
                            "root_cause_time": root_cause_time,
                            "uploader_id": st.session_state.get("user_id"),
                        }
                        record_id = save_record(
                            metadata,
                            st.session_state.get("image_bytes", image_bytes),
                            st.session_state.get("image_ext", ext),
                            analysis,
                        )
                        st.success(f"Record saved with ID {record_id}")
                        log_audit("record_saved", "success", {
                            "id": st.session_state.get("user_id"),
                            "username": st.session_state.get("username"),
                        })

    with tab_compare:
        require_roles(["Administrator", "Clinician", "Researcher"])
        st.subheader("Compare ECGs")
        st.caption("Each sample can come from saved records or a new upload.")

        def get_analysis_from_source(label: str, source_choice: str):
            """Load analysis from DB or run a new analysis from upload."""
            if source_choice == "From records":
                # Load available records and map the selection to an ID.
                records = load_records()
                if records.empty:
                    st.info("No records available.")
                    return None, f"ECG {label} (No records stored)"

                # Build a display->id mapping for the selectbox
                record_map = {
                    f"Record #{int(row.id)} ({row.patient_id or 'Anonymous'} - {row.ecg_datetime or 'No date'})": int(row.id)
                    for row in records.itertuples(index=False)
                }

                selection = st.selectbox(
                    f"Select ECG {label}",
                    list(record_map.keys()),
                    key=f"record_{label}",
                )
                record = load_record(record_map[selection])
                return record.get("analysis"), selection

            upload = st.file_uploader(
                f"Upload ECG {label}",
                type=["png", "jpg", "jpeg", "pdf"],
                key=f"upload_{label}",
            )
            camera_photo = st.camera_input(
                f"Take a picture of ECG {label}",
                key=f"camera_{label}",
            )
            selected_upload = upload or camera_photo
            
            if selected_upload:
                # Run analysis from the uploaded file on-demand via API.
                image, image_bytes, ext = load_image_from_upload(selected_upload)
                if image is None:
                    st.error("Unable to read the uploaded file.")
                    return None, f"Uploaded ECG {label} ({selected_upload.name})"
                grid_spacing = detect_grid_spacing_api(image_bytes)
                manual_pixels_per_mm = st.slider(
                    f"Pixels per mm ({label})",
                    min_value=5.0,
                    max_value=40.0,
                    value=20.0,
                    step=0.5,
                    key=f"ppm_{label}",
                )
                pixels_per_mm = grid_spacing if grid_spacing else manual_pixels_per_mm
                prominence_factor = st.slider(
                    f"R-peak sensitivity ({label})",
                    min_value=0.1,
                    max_value=1.0,
                    value=0.5,
                    step=0.1,
                    key=f"prom_{label}",
                )
                return build_analysis_api(image_bytes, pixels_per_mm, prominence_factor), f"Uploaded ECG {label} ({selected_upload.name})"
            return None, f"Uploaded ECG {label} (No image selected)"

        col_a, col_b = st.columns(2)
        with col_a:
            source_a = st.radio(
                "Source for ECG A",
                ["From records", "Upload new"],
                horizontal=True,
                key="source_a",
            )
            analysis_a, info_a = get_analysis_from_source("A", source_a)
        with col_b:
            source_b = st.radio(
                "Source for ECG B",
                ["From records", "Upload new"],
                horizontal=True,
                key="source_b",
            )
            analysis_b, info_b = get_analysis_from_source("B", source_b)

        if st.button("Compare"):
            valid_a = isinstance(analysis_a, dict) and bool(analysis_a.get("signal_mV"))
            valid_b = isinstance(analysis_b, dict) and bool(analysis_b.get("signal_mV"))

            if not valid_a and not valid_b:
                st.error(f"ECG Comparison failed: Both **{info_a}** and **{info_b}** do not contain valid analysis waveform data.")
            elif not valid_a:
                st.error(f"ECG Comparison failed: **{info_a}** does not contain valid analysis waveform data.")
            elif not valid_b:
                st.error(f"ECG Comparison failed: **{info_b}** does not contain valid analysis waveform data.")
            else:
                comp_res = align_and_compare_api(analysis_a=analysis_a, analysis_b=analysis_b)
                if comp_res:
                    method = comp_res.get("alignment_method")
                    st.write(f"Alignment method: {method}")

                    # Fetch visual comparison plot
                    aligned_a_list = comp_res.get("aligned_a", [])
                    aligned_b_list = comp_res.get("aligned_b", [])
                    comp_plot = render_comparison_plot_api(aligned_a_list, aligned_b_list)
                    if comp_plot:
                        st.image(comp_plot, caption="ECG Signals Comparison")

                    # Calculate delta and fetch delta plot
                    if aligned_a_list and aligned_b_list:
                        aligned_a = np.array(aligned_a_list)
                        aligned_b = np.array(aligned_b_list)
                        delta = (aligned_b - aligned_a).tolist()
                        delta_plot = render_delta_plot_api(delta)
                        if delta_plot:
                            st.image(delta_plot, caption="ECG Signals Delta (B - A)")

                    delta_metrics = comp_res.get("delta_metrics", {})
                    delta_df = pd.DataFrame(
                        [
                            {
                                "Metric": "Heart Rate (bpm)",
                                "Delta": delta_metrics.get("heart_rate_bpm"),
                            },
                            {
                                "Metric": "PR Interval (ms)",
                                "Delta": delta_metrics.get("pr_interval_ms"),
                            },
                            {
                                "Metric": "QRS Duration (ms)",
                                "Delta": delta_metrics.get("qrs_duration_ms"),
                            },
                            {
                                "Metric": "QT Interval (ms)",
                                "Delta": delta_metrics.get("qt_interval_ms"),
                            },
                        ]
                    )
                    st.dataframe(delta_df)

                    comparison_json = json.dumps(
                        {
                            "alignment_method": method,
                            "delta_metrics": delta_metrics,
                        },
                        indent=2,
                    )
                    st.download_button(
                        "Download Comparison JSON",
                        comparison_json,
                        file_name="ecg_comparison.json",
                        mime="application/json",
                        on_click=lambda: log_audit(
                            "export_comparison",
                            "success",
                            {
                                "id": st.session_state.get("user_id"),
                                "username": st.session_state.get("username"),
                            },
                        ),
                    )
                else:
                    st.error(f"ECG Comparison failed while aligning signals between **{info_a}** and **{info_b}**.")

    with tab_records:
        require_roles(["Administrator", "Clinician", "Researcher"])
        st.subheader("Saved Records")
        records = load_records()
        if records.empty:
            st.info("No records saved yet.")
        else:
            restrict_ids = get_setting("restrict_patient_identifiers", "true") == "true"
            if restrict_ids and st.session_state.get("role") == "Researcher":
                records = records.copy()
                records["patient_id"] = records["patient_id"].apply(mask_patient_id)

            is_admin = is_user_management_enabled() and user_has_role(["Administrator"])

            @st.dialog("View Record Details")
            def view_record_dialog(record_id):
                record = load_record(record_id)
                analysis = record.get("analysis")
                if analysis:
                    st.write("**Graphical Format (Waveform)**")
                    plot_bytes = render_signal_plot_api(analysis)
                    if plot_bytes:
                        st.image(plot_bytes, caption="ECG Waveform Analysis")

                    st.write("**Tabular Format (Metrics)**")
                    st.dataframe(metrics_table(analysis["metrics"]), use_container_width=True)
                else:
                    st.warning("No analysis data found for this record.")

            records_df = records.copy()
            if not records_df.empty:
                records_df.insert(0, "Select", False)

            toolbar_container = st.container()

            selected_records = pd.DataFrame()
            if not records_df.empty:
                edited_df = st.data_editor(
                    records_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Select": st.column_config.CheckboxColumn("Select", help="Select record", default=False)
                    },
                    disabled=[col for col in records_df.columns if col != "Select"],
                    key="records_table_editor"
                )
                selected_records = edited_df[edited_df["Select"] == True]

            has_selection = not selected_records.empty

            with toolbar_container:
                if is_admin:
                    col_title, col_gap, col_btn1, col_btn2 = st.columns([0.7, 0.2, 0.05, 0.05])
                else:
                    col_title, col_gap, col_btn1 = st.columns([0.7, 0.25, 0.05])

                with col_btn1:
                    if st.button("👀", help="View selected record", disabled=not has_selection):
                        if len(selected_records) > 1:
                            st.warning("Select only one record to view.")
                        else:
                            view_record_dialog(int(selected_records.iloc[0]["id"]))
                
                if is_admin:
                    with col_btn2:
                        if st.button("🗑️", help="Delete selected record(s)", disabled=not has_selection):
                            selected_ids = selected_records["id"].tolist()
                            deleted_count = 0
                            missing_count = 0
                            for record_id in selected_ids:
                                if delete_record(int(record_id)):
                                    deleted_count += 1
                                else:
                                    missing_count += 1
                            log_audit(
                                "record_deleted",
                                "success" if missing_count == 0 else "partial",
                                {
                                    "id": st.session_state.get("user_id"),
                                    "username": st.session_state.get("username"),
                                },
                                f"record_ids={selected_ids}",
                            )
                            st.success(f"Deleted {deleted_count} record(s).")
                            if missing_count:
                                st.warning(f"{missing_count} record(s) were not found (already deleted?).")
                            if "records_table_editor" in st.session_state:
                                del st.session_state["records_table_editor"]
                            st.rerun()

            if not is_admin and is_user_management_enabled():
                st.info("Only Administrators can delete records.")

    if tab_admin_obj:
        with tab_admin_obj:
            require_roles(["Administrator"])
            st.subheader("User Management")
            st.caption("Admin-only controls for access, users, and audit logs.")

            st.markdown("### Settings")
            user_mgmt_enabled = st.checkbox(
                "Enable user management",
                value=is_user_management_enabled(),
            )
            session_timeout = st.number_input(
                "Session timeout (minutes)",
                min_value=5,
                max_value=240,
                value=get_session_timeout_minutes(),
                step=5,
            )
            auth_mode = st.selectbox("Authentication mode", ["local"], index=0)
            allow_patient_storage = st.checkbox(
                "Allow storing patient identifiers",
                value=get_setting("allow_patient_data_storage", "false") == "true",
            )
            restrict_patient_ids = st.checkbox(
                "Restrict patient identifiers for Researcher role",
                value=get_setting("restrict_patient_identifiers", "true") == "true",
            )
            if st.button("Save settings"):
                # Persist all settings and log the change.
                set_setting("user_management_enabled", "true" if user_mgmt_enabled else "false")
                set_setting("session_timeout_minutes", str(int(session_timeout)))
                set_setting("auth_mode", auth_mode)
                set_setting("allow_patient_data_storage", "true" if allow_patient_storage else "false")
                set_setting("restrict_patient_identifiers", "true" if restrict_patient_ids else "false")
                log_audit(
                    "settings_updated",
                    "success",
                    {
                        "id": st.session_state.get("user_id"),
                        "username": st.session_state.get("username"),
                        },
                )
                st.success("Settings updated.")

            st.markdown("### Users")

            @st.dialog("Create New User")
            def create_user_dialog():
                new_username = st.text_input("Username")
                new_display = st.text_input("Display name")
                new_role = st.selectbox("Role", ["Administrator", "Clinician", "Researcher"], index=2)
                new_password = st.text_input("Password", type="password")
                confirm_password = st.text_input("Re-enter Password", type="password")
                new_enabled = st.checkbox("Enabled", value=True)
                if st.button("Save User", use_container_width=True):
                    if not new_username or not new_password:
                        st.error("Username and password are required.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match.")
                    else:
                        create_user(new_username, new_display, new_role, new_password, new_enabled)
                        log_audit("user_created", "success", {"id": st.session_state.get("user_id"), "username": st.session_state.get("username")}, f"user={new_username}")
                        st.rerun()

            @st.dialog("Update User")
            def update_user_dialog(user_row):
                upd_display = st.text_input("Display name", value=user_row["display_name"] or "")
                role_idx = ["Administrator", "Clinician", "Researcher"].index(user_row["role"]) if user_row["role"] in ["Administrator", "Clinician", "Researcher"] else 2
                upd_role = st.selectbox("Role", ["Administrator", "Clinician", "Researcher"], index=role_idx)
                upd_enabled = st.checkbox("Enabled", value=bool(user_row["enabled"]), help="Checked means user is enabled and allowed to perform operations")
                if st.button("Update User", use_container_width=True):
                    update_user(user_row["id"], upd_display, upd_role, upd_enabled)
                    log_audit("user_updated", "success", {"id": st.session_state.get("user_id"), "username": st.session_state.get("username")}, f"user_id={user_row['id']}")
                    if "user_editor" in st.session_state:
                        del st.session_state["user_editor"]
                    st.rerun()

            users_df = list_users()
            if not users_df.empty:
                users_df.insert(0, "Select", False)
            
            toolbar_container = st.container()
            
            selected_users = pd.DataFrame()
            if not users_df.empty:
                edited_df = st.data_editor(
                    users_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Select": st.column_config.CheckboxColumn("Select", help="Select user", default=False)
                    },
                    disabled=["id", "username", "display_name", "role", "enabled", "created_at", "updated_at"],
                    key="user_editor"
                )
                selected_users = edited_df[edited_df["Select"] == True]

            has_selection = not selected_users.empty

            with toolbar_container:
                col_title, col_gap, col_btn1, col_btn2, col_btn3 = st.columns([0.7, 0.15, 0.05, 0.05, 0.05])
                with col_btn1:
                    if st.button("➕", help="Create new user"):
                        create_user_dialog()
                with col_btn2:
                    if st.button("✏️", help="Edit selected user", disabled=not has_selection):
                        if len(selected_users) > 1:
                            st.warning("Select only one user to edit.")
                        else:
                            update_user_dialog(selected_users.iloc[0])
                with col_btn3:
                    if st.button("🗑️", help="Delete selected user(s)", disabled=not has_selection):
                        for _, row in selected_users.iterrows():
                            if row["username"] == "admin":
                                st.error("Cannot delete the default admin account.")
                                continue
                            try:
                                # We use direct function call to avoid requiring an API server restart
                                import auth
                                auth.delete_user(row["id"])
                                log_audit("user_deleted", "success", {"id": st.session_state.get("user_id"), "username": st.session_state.get("username")}, f"user_id={row['id']}")
                            except Exception as e:
                                st.error(f"Failed to delete {row['username']}: {e}")
                        st.success("Selected user(s) deleted and their records reassigned to admin.")
                        if "user_editor" in st.session_state:
                            del st.session_state["user_editor"]
                        st.rerun()

            st.markdown("### Audit Logs")
            # Filterable audit log viewer.
            log_limit = st.slider("Log entries", min_value=50, max_value=500, value=200, step=50)
            logs_df = list_audit_logs(limit=log_limit)
            st.dataframe(logs_df, use_container_width=True)

            # Data export removed from Admin tab. Use the Analysis tab for exports and table views.

    # Render the Admin-only Analysis tab if present (created only for Administrators)
    if tab_analysis:
        with tab_analysis:
            require_roles(["Administrator"])
            st.subheader("Analysis")
            st.write("Export the entire database to CSV files and a combined Excel workbook. View tables below.")

            if st.button("Export all data (Excel Workbook)"):
                try:
                    res = requests.post(f"{BACKEND_URL}/export")
                    if res.status_code == 200:
                        excel_data = res.content
                        st.download_button(
                            label="Download Excel workbook",
                            data=excel_data,
                            file_name="ecg_export.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            on_click=lambda: log_audit("export_all_data", "success", {"id": st.session_state.get("user_id"), "username": st.session_state.get("username")} ),
                        )
                        st.success("Export ready for download!")
                    else:
                        st.error(f"Export failed: server returned status code {res.status_code}")
                except Exception as e:
                    st.error(f"Export failed: {e}")
                    log_audit("export_all_data", "failure", {"id": st.session_state.get("user_id"), "username": st.session_state.get("username")}, str(e))

            # Present all database tables in-page as dataframes
            try:
                res_tables = requests.get(f"{BACKEND_URL}/tables")
                if res_tables.status_code == 200:
                    table_names = res_tables.json()
                    for table in table_names:
                        res_data = requests.get(f"{BACKEND_URL}/table/{table}")
                        if res_data.status_code == 200:
                            df = pd.DataFrame(res_data.json())
                            st.markdown(f"### {table}")
                            st.dataframe(df, use_container_width=True)
                            
                            # Render inline CSV download button for the table
                            csv_str = df.to_csv(index=False)
                            st.download_button(
                                label=f"Download {table} as CSV",
                                data=csv_str,
                                file_name=f"{table}.csv",
                                mime="text/csv",
                                key=f"csv_download_{table}"
                            )
                else:
                    st.error("Failed to load tables list from backend.")
            except Exception as e:
                st.error(f"Failed to load tables: {e}")


if __name__ == "__main__":
    main()

