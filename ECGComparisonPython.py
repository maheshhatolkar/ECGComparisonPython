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

def main():
    """Streamlit GUI entry point."""
    # Initialize UI and persistent storage.
    st.set_page_config(page_title="ECG Graph Extraction", layout="wide")
    init_db()
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
        st.warning("Default admin account exists (username: admin). Please reset the password.")

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
            prep = preprocess_image(image)
            grid_spacing = detect_grid_spacing(prep["enhanced"])
            st.write("Grid detection:")

            if grid_spacing:
                st.success(f"Detected grid spacing: {grid_spacing:.2f} px per 1 mm")
            else:
                st.warning("Automatic grid spacing failed. Please set pixels per mm manually.")

            manual_pixels_per_mm = st.slider("Pixels per mm", min_value=5.0, max_value=40.0, value=20.0, step=0.5)
            pixels_per_mm = grid_spacing if grid_spacing else manual_pixels_per_mm

            prominence_factor = st.slider("R-peak sensitivity", min_value=0.1, max_value=1.0, value=0.5, step=0.1)

            if st.button("Run Analysis"):
                # Run the full pipeline and display plots/metrics.
                analysis = build_analysis(image, pixels_per_mm, prominence_factor)
                st.session_state["analysis"] = analysis

            if "analysis" in st.session_state:
                # Render waveform plot, metrics, and export options.
                analysis = st.session_state["analysis"]
                signal = np.array(analysis["signal_mV"])

    # Admin-only Analysis tab logic moved to after the Analyze save form to avoid
    # interfering with the per-upload analysis rendering flow above.
                time_ms = np.array(analysis["time_ms"])

                fig = render_signal_plot(signal, time_ms, analysis["features"])
                st.pyplot(fig)
                plt.close(fig)
                st.dataframe(metrics_table(analysis["metrics"]))

                csv_data, json_data = analysis_to_exports(analysis)

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
                    return None

                # Build a display->id mapping for the selectbox
                # Use a concise human-readable label so users can pick the right record.
                record_map = {
                    f"{int(row.id)} - {row.patient_id or ''} - {row.ecg_datetime or ''}": int(row.id)
                    for row in records.itertuples(index=False)
                }

                selection = st.selectbox(
                    f"Select ECG {label}",
                    list(record_map.keys()),
                    key=f"record_{label}",
                )
                record = load_record(record_map[selection])
                return record.get("analysis")
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
                # Run analysis from the uploaded file on-demand.
                image, _, _ = load_image_from_upload(selected_upload)
                if image is None:
                    st.error("Unable to read the uploaded file.")
                    return None
                prep = preprocess_image(image)
                grid_spacing = detect_grid_spacing(prep["enhanced"])
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
                return build_analysis(image, pixels_per_mm, prominence_factor)
            return None

        col_a, col_b = st.columns(2)
        with col_a:
            source_a = st.radio(
                "Source for ECG A",
                ["From records", "Upload new"],
                horizontal=True,
                key="source_a",
            )
            analysis_a = get_analysis_from_source("A", source_a)
        with col_b:
            source_b = st.radio(
                "Source for ECG B",
                ["From records", "Upload new"],
                horizontal=True,
                key="source_b",
            )
            analysis_b = get_analysis_from_source("B", source_b)

        if analysis_a and analysis_b and st.button("Compare"):
            # Align, compare, and visualize deltas.
            signal_a = np.array(analysis_a["signal_mV"])
            signal_b = np.array(analysis_b["signal_mV"])
            aligned_a, aligned_b, method = align_signals(
                signal_a,
                signal_b,
                analysis_a["features"]["r_peaks"],
                analysis_b["features"]["r_peaks"],
            )
            delta = aligned_b - aligned_a

            st.write(f"Alignment method: {method}")
            fig = render_comparison_plot(aligned_a, aligned_b)
            st.pyplot(fig)
            plt.close(fig)
            fig = render_delta_plot(delta)
            st.pyplot(fig)
            plt.close(fig)

            delta_metrics = comparison_metrics(analysis_a["metrics"], analysis_b["metrics"])
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
            if is_admin:
                # Admins get a selectable "Delete" column and a single Delete button.
                editor_df = records.copy()
                editor_df.insert(0, "Delete", False)

                # Header row with a "Delete" button aligned to the Delete column.
                head_cols = st.columns([1, 3, 3, 3, 6])
                head_cols[0].write("")
                head_cols[1].markdown("**id**")
                head_cols[2].markdown("**patient_id**")
                head_cols[3].markdown("**ecg_datetime**")
                delete_clicked = head_cols[4].button("Delete", type="primary", key="delete_selected_records")

                edited = st.data_editor(
                    editor_df,
                    use_container_width=True,
                    hide_index=True,
                    disabled=[col for col in editor_df.columns if col != "Delete"],
                    column_config={
                        "Delete": st.column_config.CheckboxColumn(
                            "",
                            help="Check to mark this record for deletion.",
                            default=False,
                        ),
                    },
                    key="records_table_editor",
                )

                if delete_clicked:
                    # Require explicit confirmation before deletion.
                    selected_ids = edited.loc[edited["Delete"] == True, "id"].tolist()  # noqa: E712
                    if not selected_ids:
                        st.warning("No records selected.")
                    else:
                        confirm = st.checkbox(
                            "I understand this permanently deletes the selected record(s)",
                            key="delete_selected_confirm",
                        )
                        if not confirm:
                            st.stop()
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
                        st.rerun()
            else:
                st.dataframe(records, use_container_width=True)
                if is_user_management_enabled() and not user_has_role(["Administrator"]):
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
            users_df = list_users()
            st.dataframe(users_df, use_container_width=True)

            with st.expander("Create user"):
                # Admin flow for adding new accounts.
                new_username = st.text_input("Username", key="new_username")
                new_display = st.text_input("Display name", key="new_display")
                new_role = st.selectbox(
                    "Role",
                    ["Administrator", "Clinician", "Researcher"],
                    index=2,
                    key="new_role",
                )
                new_password = st.text_input("Password", type="password", key="new_password")
                new_enabled = st.checkbox("Enabled", value=True, key="new_enabled")
                if st.button("Create user"):
                    if not new_username or not new_password:
                        st.error("Username and password are required.")
                    else:
                        create_user(new_username, new_display, new_role, new_password, new_enabled)
                        log_audit(
                            "user_created",
                            "success",
                            {
                                "id": st.session_state.get("user_id"),
                                "username": st.session_state.get("username"),
                            },
                            f"user={new_username}",
                        )
                        st.success("User created.")
                        st.rerun()

            with st.expander("Update user"):
                # Update existing user metadata and role assignments.
                user_options = {f"{row.username} ({row.role})": row.id for row in users_df.itertuples(index=False)}
                if user_options:
                    selected_label = st.selectbox("Select user", list(user_options.keys()))
                    selected_id = user_options[selected_label]
                    selected_row = users_df[users_df["id"] == selected_id].iloc[0]
                    upd_display = st.text_input("Display name", value=selected_row["display_name"] or "")
                    upd_role = st.selectbox(
                        "Role",
                        ["Administrator", "Clinician", "Researcher"],
                        index=["Administrator", "Clinician", "Researcher"].index(selected_row["role"]),
                    )
                    upd_enabled = st.checkbox("Enabled", value=bool(selected_row["enabled"]))
                    if st.button("Update user"):
                        update_user(selected_id, upd_display, upd_role, upd_enabled)
                        log_audit(
                            "user_updated",
                            "success",
                            {
                                "id": st.session_state.get("user_id"),
                                "username": st.session_state.get("username"),
                            },
                            f"user_id={selected_id}",
                        )
                        st.success("User updated.")
                        st.rerun()

            with st.expander("Reset password"):
                # Administrative password reset flow.
                user_options = {f"{row.username}": row.id for row in users_df.itertuples(index=False)}
                if user_options:
                    selected_label = st.selectbox("Select user", list(user_options.keys()), key="reset_user")
                    selected_id = user_options[selected_label]
                    new_password = st.text_input("New password", type="password", key="reset_password")
                    if st.button("Reset password"):
                        if not new_password:
                            st.error("Password is required.")
                        else:
                            reset_password(selected_id, new_password)
                            log_audit(
                                "password_reset",
                                "success",
                                {
                                    "id": st.session_state.get("user_id"),
                                    "username": st.session_state.get("username"),
                            },
                            f"user_id={selected_id}",
                        )
                        st.success("Password reset.")

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

            if st.button("Export all data (CSV + Excel)"):
                try:
                    exports = data_export.export_all_data()
                    excel_path = exports.get("excel_file")
                    csv_files = exports.get("csv_files")
                    st.success("Export completed")
                    if excel_path and os.path.exists(excel_path):
                        with open(excel_path, "rb") as f:
                            st.download_button(
                                label="Download Excel workbook",
                                data=f.read(),
                                file_name=os.path.basename(excel_path),
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                on_click=lambda: log_audit("export_all_data", "success", {"id": st.session_state.get("user_id"), "username": st.session_state.get("username")} ),
                            )
                    if csv_files:
                        st.write("CSV files:")
                        for p in csv_files:
                            if os.path.exists(p):
                                with open(p, "rb") as f:
                                    st.download_button(label=f"Download {os.path.basename(p)}", data=f.read(), file_name=os.path.basename(p), mime="text/csv")
                except Exception as e:
                    st.error(f"Export failed: {e}")
                    log_audit("export_all_data", "failure", {"id": st.session_state.get("user_id"), "username": st.session_state.get("username")}, str(e))

            # Present all database tables in-page as dataframes
            try:
                paths = StoragePaths.current()
                conn = sqlite3.connect(paths.db_path)
                table_names = data_export._get_table_names(conn)
                for table in table_names:
                    try:
                        df = pd.read_sql_query(f"SELECT * FROM [{table}]", conn)
                    except Exception:
                        st.warning(f"Unable to read table: {table}")
                        continue
                    st.markdown(f"### {table}")
                    st.dataframe(df, use_container_width=True)
                conn.close()
            except Exception as e:
                st.error(f"Failed to load tables: {e}")


if __name__ == "__main__":
    main()

