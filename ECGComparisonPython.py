
import io
import json
import os
import sqlite3
import hashlib
from datetime import datetime

import cv2
import fitz
import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image
from scipy.signal import find_peaks, savgol_filter


# Application storage locations
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGE_DIR = os.path.join(DATA_DIR, "images")
DB_PATH = os.path.join(DATA_DIR, "ecg.db")


def ensure_storage():
	"""Ensure on-disk storage exists for images and database."""
	os.makedirs(IMAGE_DIR, exist_ok=True)


def init_db():
	"""Initialize SQLite database schema if missing."""
	ensure_storage()
	with sqlite3.connect(DB_PATH) as conn:
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS ecg_records (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				patient_id TEXT,
				ecg_datetime TEXT,
				root_cause TEXT,
				root_cause_time TEXT,
				image_filename TEXT NOT NULL,
				image_hash TEXT NOT NULL,
				analysis_json TEXT NOT NULL,
				created_at TEXT NOT NULL
			)
			"""
		)
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS ecg_comparisons (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				record_a_id INTEGER,
				record_b_id INTEGER,
				alignment_method TEXT,
				delta_json TEXT NOT NULL,
				created_at TEXT NOT NULL
			)
			"""
		)


def compute_hash(data: bytes) -> str:
	"""Compute SHA-256 hash for image deduplication and integrity."""
	return hashlib.sha256(data).hexdigest()


def save_image_bytes(image_bytes: bytes, ext: str) -> str:
	"""Persist image bytes on disk and return stored filename."""
	ensure_storage()
	image_hash = compute_hash(image_bytes)
	filename = f"{image_hash[:16]}{ext}"
	path = os.path.join(IMAGE_DIR, filename)
	if not os.path.exists(path):
		with open(path, "wb") as f:
			f.write(image_bytes)
	return filename


def load_records() -> pd.DataFrame:
	"""Load a summary list of saved ECG records for display."""
	with sqlite3.connect(DB_PATH) as conn:
		return pd.read_sql_query(
			"SELECT id, patient_id, ecg_datetime, created_at FROM ecg_records ORDER BY created_at DESC",
			conn,
		)


def load_record(record_id: int) -> dict:
	"""Load a single ECG record (including analysis payload)."""
	with sqlite3.connect(DB_PATH) as conn:
		row = conn.execute(
			"SELECT * FROM ecg_records WHERE id = ?",
			(record_id,),
		).fetchone()
	if not row:
		return {}
	columns = [
		"id",
		"patient_id",
		"ecg_datetime",
		"root_cause",
		"root_cause_time",
		"image_filename",
		"image_hash",
		"analysis_json",
		"created_at",
	]
	data = dict(zip(columns, row))
	data["analysis"] = json.loads(data["analysis_json"])
	return data


def save_record(metadata: dict, image_bytes: bytes, ext: str, analysis: dict) -> int:
	"""Store ECG metadata, image, and analysis in the database."""
	image_filename = save_image_bytes(image_bytes, ext)
	image_hash = compute_hash(image_bytes)
	created_at = datetime.utcnow().isoformat()
	with sqlite3.connect(DB_PATH) as conn:
		cur = conn.execute(
			"""
			INSERT INTO ecg_records (
				patient_id, ecg_datetime, root_cause, root_cause_time,
				image_filename, image_hash, analysis_json, created_at
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
			""" ,
			(
				metadata.get("patient_id"),
				metadata.get("ecg_datetime"),
				metadata.get("root_cause"),
				metadata.get("root_cause_time"),
				image_filename,
				image_hash,
				json.dumps(analysis),
				created_at,
			),
		)
		return cur.lastrowid


def open_pdf_first_page(pdf_bytes: bytes) -> Image.Image:
	"""Render the first page of a PDF to an RGB image."""
	doc = fitz.open(stream=pdf_bytes, filetype="pdf")
	page = doc.load_page(0)
	pix = page.get_pixmap(dpi=200)
	img_data = pix.tobytes("png")
	return Image.open(io.BytesIO(img_data)).convert("RGB")


def load_image_from_upload(uploaded_file):
	"""Read uploaded file and return (PIL image, raw bytes, file extension)."""
	if uploaded_file is None:
		return None, None, None
	data = uploaded_file.getvalue()
	filename = uploaded_file.name.lower()
	if filename.endswith(".pdf"):
		image = open_pdf_first_page(data)
		ext = ".png"
	else:
		image = Image.open(io.BytesIO(data)).convert("RGB")
		ext = os.path.splitext(filename)[1] or ".png"
	return image, data, ext


def preprocess_image(image: Image.Image) -> dict:
	"""Enhance the ECG image for digitization (denoise + contrast)."""
	rgb = np.array(image)
	gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
	denoised = cv2.medianBlur(gray, 3)
	clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
	enhanced = clahe.apply(denoised)
	return {
		"gray": gray,
		"enhanced": enhanced,
	}


def detect_grid_spacing(enhanced_gray: np.ndarray) -> float | None:
	"""Estimate grid spacing (pixels per mm) from ECG paper gridlines."""
	h, w = enhanced_gray.shape
	binary = cv2.adaptiveThreshold(
		enhanced_gray,
		255,
		cv2.ADAPTIVE_THRESH_MEAN_C,
		cv2.THRESH_BINARY_INV,
		31,
		7,
	)
	horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 40), 1))
	vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 40)))
	horizontal_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
	vertical_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)

	row_sums = horizontal_lines.sum(axis=1)
	col_sums = vertical_lines.sum(axis=0)
	row_peaks, _ = find_peaks(row_sums, distance=max(5, h // 200), prominence=row_sums.max() * 0.2)
	col_peaks, _ = find_peaks(col_sums, distance=max(5, w // 200), prominence=col_sums.max() * 0.2)

	spacings = []
	if len(row_peaks) > 2:
		spacings.extend(np.diff(np.sort(row_peaks)))
	if len(col_peaks) > 2:
		spacings.extend(np.diff(np.sort(col_peaks)))

	if not spacings:
		return None
	spacing = float(np.median(spacings))
	if spacing <= 0:
		return None
	return spacing


def digitize_waveform(enhanced_gray: np.ndarray) -> np.ndarray:
	"""Trace the ECG waveform by column-wise picking dark pixels."""
	h, w = enhanced_gray.shape
	y = np.full(w, np.nan)
	for x in range(w):
		col = enhanced_gray[:, x]
		if col.std() < 5:
			continue
		threshold = np.percentile(col, 10)
		dark_indices = np.where(col <= threshold)[0]
		if len(dark_indices) == 0:
			y[x] = np.argmin(col)
		else:
			y[x] = int(np.mean(dark_indices))

	y_series = pd.Series(y).interpolate(limit_direction="both")
	y_filled = y_series.to_numpy()
	window = max(5, (w // 200) * 2 + 1)
	y_smooth = savgol_filter(y_filled, window_length=window, polyorder=2)
	return y_smooth


def waveform_to_signal(y_pixels: np.ndarray, mV_per_pixel: float) -> np.ndarray:
	"""Convert y-pixel positions to amplitude in mV (baseline-centered)."""
	baseline = np.median(y_pixels)
	return (baseline - y_pixels) * mV_per_pixel


def detect_r_peaks(signal: np.ndarray, ms_per_pixel: float, prominence_factor: float = 0.5):
	"""Detect R-peaks using prominence and minimum distance heuristics."""
	distance = int(200 / ms_per_pixel)
	distance = max(distance, 1)
	prominence = max(0.05, float(np.std(signal) * prominence_factor))
	peaks, _ = find_peaks(signal, distance=distance, prominence=prominence)
	return peaks


def extract_features(signal: np.ndarray, ms_per_pixel: float, r_peaks: np.ndarray) -> dict:
	"""Estimate P/Q/R/S/T indices around each detected R-peak."""
	features = {
		"p_peaks": [],
		"q_peaks": [],
		"r_peaks": r_peaks.tolist(),
		"s_peaks": [],
		"t_peaks": [],
	}
	samples_per_ms = 1 / ms_per_pixel
	q_window = int(60 * samples_per_ms)
	s_window = int(60 * samples_per_ms)
	p_window = int(200 * samples_per_ms)
	t_window = int(240 * samples_per_ms)

	for r in r_peaks:
		q_start = max(r - q_window, 0)
		q_end = r
		s_start = r
		s_end = min(r + s_window, len(signal))

		q_idx = q_start + int(np.argmin(signal[q_start:q_end])) if q_end > q_start else r
		s_idx = s_start + int(np.argmin(signal[s_start:s_end])) if s_end > s_start else r

		p_start = max(q_idx - p_window, 0)
		p_end = q_idx
		t_start = s_idx
		t_end = min(s_idx + t_window, len(signal))

		p_idx = p_start + int(np.argmax(signal[p_start:p_end])) if p_end > p_start else q_idx
		t_idx = t_start + int(np.argmax(signal[t_start:t_end])) if t_end > t_start else s_idx

		features["q_peaks"].append(int(q_idx))
		features["s_peaks"].append(int(s_idx))
		features["p_peaks"].append(int(p_idx))
		features["t_peaks"].append(int(t_idx))

	return features


def compute_metrics(features: dict, ms_per_pixel: float) -> dict:
	"""Compute heart rate and interval metrics from detected features."""
	r_peaks = np.array(features["r_peaks"], dtype=int)
	if len(r_peaks) >= 2:
		rr_intervals = np.diff(r_peaks) * ms_per_pixel
		heart_rate = 60000 / np.mean(rr_intervals)
	else:
		rr_intervals = np.array([])
		heart_rate = None

	def avg_interval(a_list, b_list):
		if not a_list or not b_list:
			return None
		count = min(len(a_list), len(b_list))
		intervals = (np.array(b_list[:count]) - np.array(a_list[:count])) * ms_per_pixel
		return float(np.mean(intervals)) if len(intervals) else None

	pr_interval = avg_interval(features["p_peaks"], features["r_peaks"])
	qrs_duration = avg_interval(features["q_peaks"], features["s_peaks"])
	qt_interval = avg_interval(features["q_peaks"], features["t_peaks"])

	return {
		"heart_rate_bpm": float(heart_rate) if heart_rate is not None else None,
		"rr_intervals_ms": rr_intervals.tolist(),
		"pr_interval_ms": pr_interval,
		"qrs_duration_ms": qrs_duration,
		"qt_interval_ms": qt_interval,
	}


def build_analysis(image: Image.Image, pixels_per_mm: float, prominence_factor: float) -> dict:
	"""Run end-to-end analysis pipeline for a single ECG image."""
	prep = preprocess_image(image)
	waveform_pixels = digitize_waveform(prep["enhanced"])
	ms_per_pixel = 40 / pixels_per_mm
	mV_per_pixel = 0.1 / pixels_per_mm
	signal = waveform_to_signal(waveform_pixels, mV_per_pixel)
	r_peaks = detect_r_peaks(signal, ms_per_pixel, prominence_factor)
	features = extract_features(signal, ms_per_pixel, r_peaks)
	metrics = compute_metrics(features, ms_per_pixel)
	time_ms = (np.arange(len(signal)) * ms_per_pixel).tolist()

	return {
		"ms_per_pixel": ms_per_pixel,
		"mV_per_pixel": mV_per_pixel,
		"pixels_per_mm": pixels_per_mm,
		"signal_mV": signal.tolist(),
		"time_ms": time_ms,
		"features": features,
		"metrics": metrics,
	}


def align_signals(signal_a: np.ndarray, signal_b: np.ndarray, r_a: list, r_b: list) -> tuple:
	"""Align two signals via R-peak alignment or cross-correlation."""
	if r_a and r_b:
		shift = r_b[0] - r_a[0]
		method = "r-peak"
	else:
		corr = np.correlate(signal_b - signal_b.mean(), signal_a - signal_a.mean(), mode="full")
		shift = int(np.argmax(corr) - (len(signal_a) - 1))
		method = "cross-correlation"

	if shift > 0:
		aligned_a = signal_a
		aligned_b = signal_b[shift:]
	elif shift < 0:
		aligned_a = signal_a[-shift:]
		aligned_b = signal_b
	else:
		aligned_a = signal_a
		aligned_b = signal_b

	min_len = min(len(aligned_a), len(aligned_b))
	return aligned_a[:min_len], aligned_b[:min_len], method


def comparison_metrics(metrics_a: dict, metrics_b: dict) -> dict:
	"""Compute delta metrics between two ECG analyses."""
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


def metrics_table(metrics: dict) -> pd.DataFrame:
	"""Convert metrics dict to a table for display/export."""
	rows = []
	for key, label in [
		("heart_rate_bpm", "Heart Rate (bpm)"),
		("pr_interval_ms", "PR Interval (ms)"),
		("qrs_duration_ms", "QRS Duration (ms)"),
		("qt_interval_ms", "QT Interval (ms)"),
	]:
		rows.append({"Metric": label, "Value": metrics.get(key)})
	return pd.DataFrame(rows)


def render_signal_plot(signal: np.ndarray, time_ms: np.ndarray, features: dict | None = None):
	"""Plot ECG waveform with optional feature markers."""
	import matplotlib.pyplot as plt

	fig, ax = plt.subplots()
	ax.plot(time_ms, signal, label="ECG")
	if features:
		for label, color, key in [
			("P", "purple", "p_peaks"),
			("Q", "orange", "q_peaks"),
			("R", "red", "r_peaks"),
			("S", "green", "s_peaks"),
			("T", "blue", "t_peaks"),
		]:
			idx = np.array(features.get(key, []), dtype=int)
			if len(idx):
				ax.scatter(time_ms[idx], signal[idx], label=label, s=12)
	ax.set_xlabel("Time (ms)")
	ax.set_ylabel("Amplitude (mV)")
	ax.legend(loc="upper right")
	ax.grid(True, alpha=0.3)
	return fig


def render_comparison_plot(signal_a, signal_b):
	"""Plot two aligned ECG signals for visual comparison."""
	import matplotlib.pyplot as plt

	fig, ax = plt.subplots()
	ax.plot(signal_a, label="ECG-A", color="blue")
	ax.plot(signal_b, label="ECG-B", color="red", alpha=0.7)
	ax.set_xlabel("Sample")
	ax.set_ylabel("Amplitude (mV)")
	ax.legend(loc="upper right")
	ax.grid(True, alpha=0.3)
	return fig


def render_delta_plot(delta):
	"""Plot the delta waveform (ECG-B − ECG-A)."""
	import matplotlib.pyplot as plt

	fig, ax = plt.subplots()
	ax.plot(delta, label="Delta (B - A)", color="black")
	ax.axhline(0, color="gray", linestyle="--", linewidth=1)
	ax.set_xlabel("Sample")
	ax.set_ylabel("Amplitude (mV)")
	ax.legend(loc="upper right")
	ax.grid(True, alpha=0.3)
	return fig


def analysis_to_exports(analysis: dict) -> tuple[str, str]:
	"""Build CSV and JSON exports from analysis results."""
	csv_df = metrics_table(analysis["metrics"])
	csv_data = csv_df.to_csv(index=False)
	json_data = json.dumps(analysis, indent=2)
	return csv_data, json_data


def main():
	"""Streamlit GUI entry point."""
	st.set_page_config(page_title="ECG Graph Extraction", layout="wide")
	init_db()

	st.title("ECG Graph Extraction and Analysis")
	st.caption("Upload ECG images, digitize waveforms, extract features, compare, and store results.")

	tab_analyze, tab_compare, tab_records = st.tabs(["Analyze", "Compare", "Records"])

	with tab_analyze:
		st.subheader("Analyze ECG Image")
		uploaded = st.file_uploader("Upload ECG image (PNG/JPG/PDF)", type=["png", "jpg", "jpeg", "pdf"])

		if uploaded:
			image, image_bytes, ext = load_image_from_upload(uploaded)
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

			prominence_factor = st.slider("R-peak sensitivity", min_value=0.1, max_value=1.5, value=0.5, step=0.1)

			if st.button("Run Analysis"):
				# Run the full pipeline and display plots/metrics.
				analysis = build_analysis(image, pixels_per_mm, prominence_factor)
				st.session_state["analysis"] = analysis

			if "analysis" in st.session_state:
				analysis = st.session_state["analysis"]
				signal = np.array(analysis["signal_mV"])
				time_ms = np.array(analysis["time_ms"])

				st.pyplot(render_signal_plot(signal, time_ms, analysis["features"]))
				st.dataframe(metrics_table(analysis["metrics"]))

				csv_data, json_data = analysis_to_exports(analysis)
				st.download_button("Download CSV", csv_data, file_name="ecg_metrics.csv", mime="text/csv")
				st.download_button("Download JSON", json_data, file_name="ecg_analysis.json", mime="application/json")

				with st.form("save_record"):
					st.write("Save to database")
					patient_id = st.text_input("Patient ID")
					ecg_datetime = st.text_input("ECG date/time")
					root_cause = st.text_input("Possible root cause")
					root_cause_time = st.text_input("Time of root cause")
					submitted = st.form_submit_button("Save record")
					if submitted:
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

	with tab_compare:
		st.subheader("Compare ECGs")
		st.caption("Each sample can come from saved records or a new upload.")

		def get_analysis_from_source(label: str, source_choice: str):
			"""Load analysis from DB or run a new analysis from upload."""
			if source_choice == "From records":
				records = load_records()
				if records.empty:
					st.info("No records available.")
					return None
				record_map = {
					f"{row.id} | {row.patient_id} | {row.ecg_datetime}": row.id
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
			if upload:
				image, _, _ = load_image_from_upload(upload)
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
					max_value=1.5,
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
			st.pyplot(render_comparison_plot(aligned_a, aligned_b))
			st.pyplot(render_delta_plot(delta))

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
			)

	with tab_records:
		st.subheader("Saved Records")
		records = load_records()
		if records.empty:
			st.info("No records saved yet.")
		else:
			st.dataframe(records, use_container_width=True)


if __name__ == "__main__":
	main()

