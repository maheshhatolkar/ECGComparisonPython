
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


# Application storage locations and metadata used by the database layer.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGE_DIR = os.path.join(DATA_DIR, "images")
DB_PATH = os.path.join(DATA_DIR, "ecg.db")


def ensure_storage():
	"""Ensure on-disk storage exists for images and database."""
	os.makedirs(IMAGE_DIR, exist_ok=True)


@dataclass(frozen=True)
class StoragePaths:
	base_dir: str
	data_dir: str
	image_dir: str
	db_path: str

	@classmethod
	def current(cls) -> "StoragePaths":
		# Read module-level paths at call-time so tests can monkeypatch them.
		return cls(
			base_dir=BASE_DIR,
			data_dir=DATA_DIR,
			image_dir=IMAGE_DIR,
			db_path=DB_PATH,
		)


class ECGDatabase:
	def __init__(self, paths: StoragePaths, schema_version: int = DB_SCHEMA_VERSION):
		self._paths = paths
		self._schema_version = int(schema_version)

	@property
	def paths(self) -> StoragePaths:
		return self._paths

	def ensure_storage(self) -> None:
		# Create the on-disk folders needed for image storage.
		os.makedirs(self._paths.image_dir, exist_ok=True)

	def connect(self) -> sqlite3.Connection:
		# Open a connection with foreign keys enforced.
		self.ensure_storage()
		conn = sqlite3.connect(self._paths.db_path)
		conn.execute("PRAGMA foreign_keys = ON")
		return conn

	def get_db_schema_version(self, conn: sqlite3.Connection) -> int:
		# Read schema version from the settings table if it exists.
		try:
			row = conn.execute("SELECT value FROM app_settings WHERE key = ?", ("schema_version",)).fetchone()
		except sqlite3.Error:
			return 0
		if not row:
			return 0
		try:
			return int(row[0])
		except (TypeError, ValueError):
			return 0

	def set_db_schema_version(self, conn: sqlite3.Connection, version: int) -> None:
		# Persist the current schema version for future migrations.
		conn.execute(
			"INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
			("schema_version", str(int(version))),
		)

	def ecg_comparisons_has_foreign_keys(self, conn: sqlite3.Connection) -> bool:
		try:
			rows = conn.execute("PRAGMA foreign_key_list(ecg_comparisons)").fetchall()
		except sqlite3.Error:
			return False
		return bool(rows)

	def migrate_db(self, conn: sqlite3.Connection) -> None:
		current = self.get_db_schema_version(conn)
		if current >= self._schema_version:
			return

		# v1 -> v2: ensure ecg_comparisons has foreign keys for cascading deletes.
		if current < 2:
			row = conn.execute(
				"SELECT name FROM sqlite_master WHERE type='table' AND name='ecg_comparisons'",
			).fetchone()
			if not row:
				conn.execute(
					"""
					CREATE TABLE ecg_comparisons (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						record_a_id INTEGER NOT NULL,
						record_b_id INTEGER NOT NULL,
						alignment_method TEXT,
						delta_json TEXT NOT NULL,
						created_at TEXT NOT NULL,
						FOREIGN KEY(record_a_id) REFERENCES ecg_records(id) ON DELETE CASCADE,
						FOREIGN KEY(record_b_id) REFERENCES ecg_records(id) ON DELETE CASCADE
					)
					"""
				)
			elif not self.ecg_comparisons_has_foreign_keys(conn):
				conn.execute("ALTER TABLE ecg_comparisons RENAME TO ecg_comparisons_old")
				conn.execute(
					"""
					CREATE TABLE ecg_comparisons (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						record_a_id INTEGER NOT NULL,
						record_b_id INTEGER NOT NULL,
						alignment_method TEXT,
						delta_json TEXT NOT NULL,
						created_at TEXT NOT NULL,
						FOREIGN KEY(record_a_id) REFERENCES ecg_records(id) ON DELETE CASCADE,
						FOREIGN KEY(record_b_id) REFERENCES ecg_records(id) ON DELETE CASCADE
					)
					"""
				)
				conn.execute(
					"""
					INSERT INTO ecg_comparisons (id, record_a_id, record_b_id, alignment_method, delta_json, created_at)
					SELECT id, record_a_id, record_b_id, alignment_method, delta_json, created_at
					FROM ecg_comparisons_old
					"""
				)
				conn.execute("DROP TABLE ecg_comparisons_old")

			self.set_db_schema_version(conn, 2)

		self.set_db_schema_version(conn, self._schema_version)

	def create_indexes(self, conn: sqlite3.Connection) -> None:
		# Helpful indexes for common queries in the UI.
		conn.execute("CREATE INDEX IF NOT EXISTS idx_ecg_records_created_at ON ecg_records(created_at)")
		conn.execute("CREATE INDEX IF NOT EXISTS idx_ecg_comparisons_record_a ON ecg_comparisons(record_a_id)")
		conn.execute("CREATE INDEX IF NOT EXISTS idx_ecg_comparisons_record_b ON ecg_comparisons(record_b_id)")
		conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp)")

	def seed_default_settings(self, conn: sqlite3.Connection) -> None:
		# Baseline settings keep functionality disabled until admins opt in.
		defaults = {
			"user_management_enabled": "false",
			"session_timeout_minutes": "30",
			"auth_mode": "local",
			"allow_patient_data_storage": "false",
			"restrict_patient_identifiers": "true",
		}
		for key, value in defaults.items():
			conn.execute(
				"INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
				(key, value),
			)

	def seed_default_admin(self, conn: sqlite3.Connection) -> None:
		# Ensure at least one admin account exists on first run.
		row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
		if row and row[0] > 0:
			return
		password = "admin"
		salt = secrets.token_hex(16)
		password_hash = hash_password(password, salt)
		now = datetime.utcnow().isoformat()
		conn.execute(
			"""
			INSERT INTO users (username, display_name, role, password_hash, password_salt, enabled, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, 1, ?, ?)
			""",
			("admin", "Administrator", "Administrator", password_hash, salt, now, now),
		)
		conn.execute(
			"INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
			("default_admin_created", "true"),
		)

	def init_db(self) -> None:
		# Initialize core tables and apply migrations as needed.
		self.ensure_storage()
		with self.connect() as conn:
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
				CREATE TABLE IF NOT EXISTS users (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					username TEXT UNIQUE NOT NULL,
					display_name TEXT,
					role TEXT NOT NULL,
					password_hash TEXT NOT NULL,
					password_salt TEXT NOT NULL,
					enabled INTEGER NOT NULL DEFAULT 1,
					created_at TEXT NOT NULL,
					updated_at TEXT NOT NULL
				)
				"""
			)
			conn.execute(
				"""
				CREATE TABLE IF NOT EXISTS ecg_comparisons (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					alignment_method TEXT,
					delta_json TEXT NOT NULL,
				)
				"""
			)
			conn.execute(
				"""
				CREATE TABLE IF NOT EXISTS audit_logs (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					timestamp TEXT NOT NULL,
					event_type TEXT NOT NULL,
					user_id INTEGER,
					username TEXT,
					outcome TEXT NOT NULL,
					details TEXT
				)
				"""
			)
			conn.execute(
				"""
				CREATE TABLE IF NOT EXISTS app_settings (
					key TEXT PRIMARY KEY,
					value TEXT NOT NULL
				)
				"""
			)

			self.migrate_db(conn)
			self.create_indexes(conn)
			self.seed_default_settings(conn)
			self.seed_default_admin(conn)
			conn.execute(
				"INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
				("schema_version", str(self._schema_version)),
			)

	def get_setting(self, key: str, default: str | None = None) -> str | None:
		# Return a configuration value, falling back to a default.
		with sqlite3.connect(self._paths.db_path) as conn:
			row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
		if row:
			return row[0]
		return default

	def set_setting(self, key: str, value: str) -> None:
		# Upsert a configuration value in the settings table.
		with sqlite3.connect(self._paths.db_path) as conn:
			conn.execute(
				"INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
				(key, value),
			)

	def compute_hash(self, data: bytes) -> str:
		# SHA-256 hash used for image deduplication.
		return hashlib.sha256(data).hexdigest()

	def save_image_bytes(self, image_bytes: bytes, ext: str) -> str:
		# Store image bytes on disk and return the filename.
		self.ensure_storage()
		image_hash = self.compute_hash(image_bytes)
		filename = f"{image_hash[:16]}{ext}"
		if not os.path.exists(path):
			with open(path, "wb") as f:
				f.write(image_bytes)
		return filename

	def load_records(self) -> pd.DataFrame:
		# Load summarized record list for the Records tab.
		with sqlite3.connect(self._paths.db_path) as conn:
			return pd.read_sql_query(
				"SELECT id, patient_id, ecg_datetime, created_at FROM ecg_records ORDER BY created_at DESC",
				conn,
			)

	def load_record(self, record_id: int) -> dict:
		# Fetch a single record with its stored analysis payload.
		with sqlite3.connect(self._paths.db_path) as conn:
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

	def save_record(self, metadata: dict, image_bytes: bytes, ext: str, analysis: dict) -> int:
		# Persist metadata, image, and analysis results in a single row.
		image_filename = self.save_image_bytes(image_bytes, ext)
		image_hash = self.compute_hash(image_bytes)
		created_at = datetime.utcnow().isoformat()
			cur = conn.execute(
				"""
				INSERT INTO ecg_records (
					patient_id, ecg_datetime, root_cause, root_cause_time,
					image_filename, image_hash, analysis_json, created_at
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

	def delete_record(self, record_id: int) -> bool:
		# Remove record and related comparisons; clean up image if unused.
		self.ensure_storage()
		image_filename: str | None = None
		with sqlite3.connect(self._paths.db_path) as conn:
			conn.execute("PRAGMA foreign_keys = ON")
			row = conn.execute(
				"SELECT image_filename FROM ecg_records WHERE id = ?",
				(record_id,),
			).fetchone()
			if not row:
				return False
			image_filename = row[0]
			conn.execute(
				"DELETE FROM ecg_comparisons WHERE record_a_id = ? OR record_b_id = ?",
				(record_id, record_id),
			)
			conn.execute("DELETE FROM ecg_records WHERE id = ?", (record_id,))

		if image_filename:
			# Only remove the image file if no other records reference it.
			with sqlite3.connect(self._paths.db_path) as conn:
				count_row = conn.execute(
					"SELECT COUNT(*) FROM ecg_records WHERE image_filename = ?",
					(image_filename,),
				).fetchone()
			remaining = int(count_row[0]) if count_row else 0
			if remaining == 0:
				path = os.path.join(self._paths.image_dir, image_filename)
				try:
					if os.path.exists(path):
						os.remove(path)
				except OSError:
					pass



class ECGAnalyzer:
	def preprocess_image(self, image: Image.Image) -> dict:
		# Convert to grayscale and apply denoise + contrast enhancement.
		rgb = np.array(image)
		gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
		denoised = cv2.medianBlur(gray, 3)
		clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
		enhanced = clahe.apply(denoised)
		return {
			"gray": gray,
			"enhanced": enhanced,
		}

	def detect_grid_spacing(self, enhanced_gray: np.ndarray) -> float | None:
		# Detect gridlines and estimate median spacing between them.
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

	def digitize_waveform(self, enhanced_gray: np.ndarray) -> np.ndarray:
		# Trace the darkest pixel per column to build a waveform.
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

	def waveform_to_signal(self, y_pixels: np.ndarray, mV_per_pixel: float) -> np.ndarray:
		# Convert from image coordinates to baseline-centered amplitudes.
		baseline = np.median(y_pixels)
		return (baseline - y_pixels) * mV_per_pixel

	def detect_r_peaks(self, signal: np.ndarray, ms_per_pixel: float, prominence_factor: float = 0.5):
		# Use distance and prominence heuristics to find R-peaks.
		distance = int(200 / ms_per_pixel)
		distance = max(distance, 1)
		prominence = max(0.05, float(np.std(signal) * prominence_factor))
		peaks, _ = find_peaks(signal, distance=distance, prominence=prominence)
		return peaks

	def extract_features(self, signal: np.ndarray, ms_per_pixel: float, r_peaks: np.ndarray) -> dict:
		# For each R-peak, infer neighboring P/Q/S/T points using windows.
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

	def compute_metrics(self, features: dict, ms_per_pixel: float) -> dict:
		# Calculate heart rate and interval metrics from detected indices.
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

	def build_analysis(self, image: Image.Image, pixels_per_mm: float, prominence_factor: float) -> dict:
		# Run full pipeline: preprocess -> digitize -> detect peaks -> metrics.
		prep = self.preprocess_image(image)
		waveform_pixels = self.digitize_waveform(prep["enhanced"])
		ms_per_pixel = 40 / pixels_per_mm
		mV_per_pixel = 0.1 / pixels_per_mm
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


class ECGAligner:
	def align_signals(self, signal_a: np.ndarray, signal_b: np.ndarray, r_a: list, r_b: list) -> tuple:
		# Align by first R-peak if available, else use cross-correlation.
		if r_a and r_b:
			shift = r_b[0] - r_a[0]
			method = "r-peak"
		else:
			corr = np.correlate(signal_b - signal_b.mean(), signal_a - signal_a.mean(), mode="full")
			shift = int(np.argmax(corr) - (len(signal_a) - 1))
			method = "cross-correlation"

		if shift > 0:
			# Remove leading samples from signal_b.
			aligned_a = signal_a
			aligned_b = signal_b[shift:]
		elif shift < 0:
			# Remove leading samples from signal_a.
			aligned_a = signal_a[-shift:]
			aligned_b = signal_b
		else:
			aligned_a = signal_a
			aligned_b = signal_b

		min_len = min(len(aligned_a), len(aligned_b))
		return aligned_a[:min_len], aligned_b[:min_len], method


class ECGExporter:
	def metrics_table(self, metrics: dict) -> pd.DataFrame:
		# Convert metrics into a tabular format for display/export.
		rows = []
		for key, label in [
			("heart_rate_bpm", "Heart Rate (bpm)"),
			("pr_interval_ms", "PR Interval (ms)"),
			("qrs_duration_ms", "QRS Duration (ms)"),
			("qt_interval_ms", "QT Interval (ms)"),
		]:
			rows.append({"Metric": label, "Value": metrics.get(key)})
		return pd.DataFrame(rows)

	def analysis_to_exports(self, analysis: dict) -> tuple[str, str]:
		# Build CSV and JSON payloads for downloads.
		csv_df = self.metrics_table(analysis["metrics"])
		csv_data = csv_df.to_csv(index=False)
		json_data = json.dumps(analysis, indent=2)
		return csv_data, json_data


def _db() -> ECGDatabase:
	# Lazy wrapper for the database layer.
	return ECGDatabase(StoragePaths.current())


def _analyzer() -> ECGAnalyzer:
	# Lazy wrapper for the image analysis layer.
	return ECGAnalyzer()


def _aligner() -> ECGAligner:
	# Lazy wrapper for the signal alignment helper.
	return ECGAligner()


def _exporter() -> ECGExporter:
	# Lazy wrapper for the export helper.
	return ECGExporter()


def ensure_storage():
	"""Ensure on-disk storage exists for images and database."""
	_db().ensure_storage()


def init_db():
	"""Initialize SQLite database schema if missing."""
	_db().init_db()


def get_db_schema_version(conn: sqlite3.Connection) -> int:
	return _db().get_db_schema_version(conn)


def set_db_schema_version(conn: sqlite3.Connection, version: int) -> None:
	_db().set_db_schema_version(conn, version)


def ecg_comparisons_has_foreign_keys(conn: sqlite3.Connection) -> bool:
	return _db().ecg_comparisons_has_foreign_keys(conn)


def migrate_db(conn: sqlite3.Connection) -> None:
	"""Apply lightweight migrations to bring DB schema up to date."""
	_db().migrate_db(conn)


def create_indexes(conn: sqlite3.Connection) -> None:
	# Performance helpers; safe to run repeatedly.
	_db().create_indexes(conn)


def seed_default_settings(conn: sqlite3.Connection):
	_db().seed_default_settings(conn)


def seed_default_admin(conn: sqlite3.Connection):
	_db().seed_default_admin(conn)


def get_setting(key: str, default: str | None = None) -> str | None:
	return _db().get_setting(key, default)


def set_setting(key: str, value: str) -> None:
	_db().set_setting(key, value)


def hash_password(password: str, salt: str) -> str:
	# Salted hash for password storage.
	return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
	# Check a plaintext password against the stored hash.
	return hash_password(password, salt) == password_hash


def get_user_by_username(username: str) -> dict | None:
	# Retrieve user credentials and profile by username.
	with sqlite3.connect(DB_PATH) as conn:
		row = conn.execute(
			"SELECT id, username, display_name, role, password_hash, password_salt, enabled FROM users WHERE username = ?",
			(username,),
		).fetchone()
	if not row:
		return None
	return {
		"id": row[0],
		"username": row[1],
		"display_name": row[2],
		"role": row[3],
		"password_hash": row[4],
		"password_salt": row[5],
		"enabled": bool(row[6]),
	}


def list_users() -> pd.DataFrame:
	# List user accounts for the admin view.
	with sqlite3.connect(DB_PATH) as conn:
		return pd.read_sql_query(
			"SELECT id, username, display_name, role, enabled, created_at, updated_at FROM users ORDER BY username",
			conn,
		)


def create_user(username: str, display_name: str, role: str, password: str, enabled: bool = True) -> None:
	# Create a new user with salted password hash.
	salt = secrets.token_hex(16)
	password_hash = hash_password(password, salt)
	now = datetime.utcnow().isoformat()
	with sqlite3.connect(DB_PATH) as conn:
		conn.execute(
			"""
			INSERT INTO users (username, display_name, role, password_hash, password_salt, enabled, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(username, display_name, role, password_hash, salt, 1 if enabled else 0, now, now),
		)


def update_user(user_id: int, display_name: str, role: str, enabled: bool) -> None:
	# Update profile metadata and enabled state.
	now = datetime.utcnow().isoformat()
	with sqlite3.connect(DB_PATH) as conn:
		conn.execute(
			"""
			UPDATE users SET display_name = ?, role = ?, enabled = ?, updated_at = ? WHERE id = ?
			""",
			(display_name, role, 1 if enabled else 0, now, user_id),
		)


def reset_password(user_id: int, new_password: str) -> None:
	# Reset a user password with a new salt.
	salt = secrets.token_hex(16)
	password_hash = hash_password(new_password, salt)
	now = datetime.utcnow().isoformat()
	with sqlite3.connect(DB_PATH) as conn:
		conn.execute(
			"""
			UPDATE users SET password_hash = ?, password_salt = ?, updated_at = ? WHERE id = ?
			""",
			(password_hash, salt, now, user_id),
		)


def log_audit(event_type: str, outcome: str, user: dict | None = None, details: str | None = None) -> None:
	# Insert a row into audit_logs for security and traceability.
	with sqlite3.connect(DB_PATH) as conn:
		conn.execute(
			"""
			INSERT INTO audit_logs (timestamp, event_type, user_id, username, outcome, details)
			VALUES (?, ?, ?, ?, ?, ?)
			""",
			(
				datetime.utcnow().isoformat(),
				event_type,
				user.get("id") if user else None,
				user.get("username") if user else None,
				outcome,
				details,
			),
		)


def list_audit_logs(limit: int = 200) -> pd.DataFrame:
	# Return the latest audit rows for the admin view.
	with sqlite3.connect(DB_PATH) as conn:
		return pd.read_sql_query(
			"SELECT timestamp, event_type, username, outcome, details FROM audit_logs ORDER BY id DESC LIMIT ?",
			conn,
			params=(limit,),
		)


def authenticate_user(username: str, password: str) -> dict | None:
	# Validate username/password against stored credentials.
	user = get_user_by_username(username)
	if not user or not user.get("enabled"):
		return None
	if verify_password(password, user["password_salt"], user["password_hash"]):
		return user
	return None


def is_user_management_enabled() -> bool:
	# Feature flag for access control and auditing.
	return get_setting("user_management_enabled", "false") == "true"


def get_session_timeout_minutes() -> int:
	# Defensive parsing of the session timeout setting.
	value = get_setting("session_timeout_minutes", "30")
	try:
		return int(value)
	except ValueError:
		return 30


def user_has_role(roles: list[str]) -> bool:
	# Helper for checking the current session's role.
	role = st.session_state.get("role")
	return role in roles


def require_roles(roles: list[str]):
	# Gate UI sections based on authentication and role.
	if not is_user_management_enabled():
		return
	if not st.session_state.get("authenticated"):
		st.warning("Please log in to continue.")
		st.stop()
	if not user_has_role(roles):
		st.error("You do not have permission to access this area.")
		st.stop()


def enforce_session_timeout():
	# Clear authentication when idle time exceeds configured threshold.
	if not is_user_management_enabled():
		return
	if not st.session_state.get("authenticated"):
		return
	last_activity = st.session_state.get("last_activity")
	if not last_activity:
		st.session_state["last_activity"] = datetime.utcnow().isoformat()
		return
	try:
		last_ts = datetime.fromisoformat(last_activity)
	except ValueError:
		last_ts = datetime.utcnow()
	minutes = get_session_timeout_minutes()
	if (datetime.utcnow() - last_ts).total_seconds() > minutes * 60:
		user = {
			"id": st.session_state.get("user_id"),
			"username": st.session_state.get("username"),
		}
		log_audit("session_timeout", "success", user)
		st.session_state.clear()
		st.warning("Session timed out. Please log in again.")
		st.stop()
	st.session_state["last_activity"] = datetime.utcnow().isoformat()


def compute_hash(data: bytes) -> str:
	"""Compute SHA-256 hash for image deduplication and integrity."""
	return _db().compute_hash(data)


def save_image_bytes(image_bytes: bytes, ext: str) -> str:
	"""Persist image bytes on disk and return stored filename."""
	return _db().save_image_bytes(image_bytes, ext)


def load_records() -> pd.DataFrame:
	"""Load a summary list of saved ECG records for display."""
	return _db().load_records()


def mask_patient_id(value: str | None) -> str | None:
	# Mask patient identifiers for restricted roles.
	if not value:
		return value
	return "***"


def load_record(record_id: int) -> dict:
	"""Load a single ECG record (including analysis payload)."""
	return _db().load_record(record_id)


def save_record(metadata: dict, image_bytes: bytes, ext: str, analysis: dict) -> int:
	"""Store ECG metadata, image, and analysis in the database."""
	return _db().save_record(metadata, image_bytes, ext, analysis)


def delete_record(record_id: int) -> bool:
	"""Delete an ECG record and any related comparisons.

	Also deletes the associated image file if no other records reference it.
	Returns True if a record was deleted, False if the record did not exist.
	"""
	return _db().delete_record(record_id)


def open_pdf_first_page(pdf_bytes: bytes) -> Image.Image:
	"""Render the first page of a PDF to an RGB image."""
	# Streamlit uploads PDF bytes; fitz renders to PNG.
	doc = fitz.open(stream=pdf_bytes, filetype="pdf")
	page = doc.load_page(0)
	pix = page.get_pixmap(dpi=200)
	img_data = pix.tobytes("png")
	return Image.open(io.BytesIO(img_data)).convert("RGB")


def load_image_from_upload(uploaded_file):
	"""Read uploaded file and return (PIL image, raw bytes, file extension)."""
	if uploaded_file is None:
		return None, None, None
	# Preserve raw bytes for hashing and disk storage.
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
	return _analyzer().preprocess_image(image)


def detect_grid_spacing(enhanced_gray: np.ndarray) -> float | None:
	"""Estimate grid spacing (pixels per mm) from ECG paper gridlines."""
	return _analyzer().detect_grid_spacing(enhanced_gray)


def digitize_waveform(enhanced_gray: np.ndarray) -> np.ndarray:
	"""Trace the ECG waveform by column-wise picking dark pixels."""
	return _analyzer().digitize_waveform(enhanced_gray)


def waveform_to_signal(y_pixels: np.ndarray, mV_per_pixel: float) -> np.ndarray:
	"""Convert y-pixel positions to amplitude in mV (baseline-centered)."""
	return _analyzer().waveform_to_signal(y_pixels, mV_per_pixel)


def detect_r_peaks(signal: np.ndarray, ms_per_pixel: float, prominence_factor: float = 0.5):
	"""Detect R-peaks using prominence and minimum distance heuristics."""
	return _analyzer().detect_r_peaks(signal, ms_per_pixel, prominence_factor)


def extract_features(signal: np.ndarray, ms_per_pixel: float, r_peaks: np.ndarray) -> dict:
	"""Estimate P/Q/R/S/T indices around each detected R-peak."""
	return _analyzer().extract_features(signal, ms_per_pixel, r_peaks)


def compute_metrics(features: dict, ms_per_pixel: float) -> dict:
	"""Compute heart rate and interval metrics from detected features."""
	return _analyzer().compute_metrics(features, ms_per_pixel)


def build_analysis(image: Image.Image, pixels_per_mm: float, prominence_factor: float) -> dict:
	"""Run end-to-end analysis pipeline for a single ECG image."""
	return _analyzer().build_analysis(image, pixels_per_mm, prominence_factor)


def align_signals(signal_a: np.ndarray, signal_b: np.ndarray, r_a: list, r_b: list) -> tuple:
	"""Align two signals via R-peak alignment or cross-correlation."""
	return _aligner().align_signals(signal_a, signal_b, r_a, r_b)


def comparison_metrics(metrics_a: dict, metrics_b: dict) -> dict:
	"""Compute delta metrics between two ECG analyses."""
	# Use None when either side is missing so deltas are not misleading.
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


def render_signal_plot(signal: np.ndarray, time_ms: np.ndarray, features: dict | None = None):
	"""Plot ECG waveform with optional feature markers."""
	import matplotlib.pyplot as plt

	fig, ax = plt.subplots()
	# Line plot of the waveform and optional feature markers.
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
	# Overlay aligned samples for a quick visual comparison.
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
	# Visualize differences as a signed delta signal.
	ax.plot(delta, label="Delta (B - A)", color="black")
	ax.axhline(0, color="gray", linestyle="--", linewidth=1)
	ax.set_xlabel("Sample")
	ax.set_ylabel("Amplitude (mV)")
	ax.legend(loc="upper right")
	ax.grid(True, alpha=0.3)
	return fig


def analysis_to_exports(analysis: dict) -> tuple[str, str]:
	"""Build CSV and JSON exports from analysis results."""


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
							st.session_state["last_activity"] = datetime.utcnow().isoformat()
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

	tab_analyze, tab_compare, tab_records, *tab_admin = st.tabs(base_tabs)

	with tab_analyze:
		require_roles(["Administrator", "Clinician", "Researcher"])
		st.subheader("Analyze ECG Image")
		uploaded = st.file_uploader("Upload ECG image (PNG/JPG/PDF)", type=["png", "jpg", "jpeg", "pdf"])

		if uploaded:
			# Load uploaded file and reset cached analysis on file change.
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
				# Render waveform plot, metrics, and export options.
				analysis = st.session_state["analysis"]
				signal = np.array(analysis["signal_mV"])
				time_ms = np.array(analysis["time_ms"])

				st.pyplot(render_signal_plot(signal, time_ms, analysis["features"]))
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
				# Run analysis from the uploaded file on-demand.
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

	if tab_admin:
		with tab_admin[0]:
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


if __name__ == "__main__":
	main()

