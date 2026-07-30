import os
import json
import sqlite3
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
import pandas as pd
import functools

DB_SCHEMA_VERSION = 3
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

class ECGDatabase:
    # The ECGDatabase class encapsulates all persistence concerns for the
    # application. It is responsible for:
    # - Ensuring on-disk storage for images exists
    # - Managing the SQLite connection and PRAGMA settings (foreign keys)
    # - Creating and migrating tables safely across schema versions
    # - Providing small helper methods used by the UI and tests to load,
    #   save and delete records in a transactional and consistent way.
    #
    # The instance is deliberately lightweight: callers should use the
    # module-level StoragePaths.current() helper to provide path configuration
    # so tests can monkeypatch locations without changing global constants.
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
                try:
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
                except sqlite3.Error as e:
                    conn.execute("DROP TABLE IF EXISTS ecg_comparisons")
                    conn.execute("ALTER TABLE ecg_comparisons_old RENAME TO ecg_comparisons")
                    raise RuntimeError(f"Failed to migrate ecg_comparisons table: {e}")

            self.set_db_schema_version(conn, 2)

        # v2 -> v3: expand analysis_json and delta_json into separate columns
        if current < 3:
            try:
                # 1. Migrate ecg_records if needed
                cursor = conn.execute("PRAGMA table_info(ecg_records)")
                has_analysis_json = any(row[1] == "analysis_json" for row in cursor.fetchall())
                if has_analysis_json:
                    conn.execute("ALTER TABLE ecg_records RENAME TO ecg_records_old")
                    conn.execute(
                        """
                        CREATE TABLE ecg_records (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            patient_id TEXT,
                            ecg_datetime TEXT,
                            root_cause TEXT,
                            root_cause_time TEXT,
                            image_filename TEXT NOT NULL,
                            image_hash TEXT NOT NULL,
                            ms_per_pixel REAL,
                            mV_per_pixel REAL,
                            pixels_per_mm REAL,
                            signal_mV TEXT,
                            time_ms TEXT,
                            p_peaks TEXT,
                            q_peaks TEXT,
                            r_peaks TEXT,
                            s_peaks TEXT,
                            t_peaks TEXT,
                            heart_rate_bpm REAL,
                            rr_intervals_ms TEXT,
                            pr_interval_ms REAL,
                            qrs_duration_ms REAL,
                            qt_interval_ms REAL,
                            created_at TEXT NOT NULL
                        )
                        """
                    )

                    cursor = conn.execute("SELECT id, patient_id, ecg_datetime, root_cause, root_cause_time, image_filename, image_hash, analysis_json, created_at FROM ecg_records_old")
                    for row in cursor.fetchall():
                        rec_id, p_id, ecg_dt, rc, rc_time, img_fn, img_hash, anal_json, created = row

                        ms_px, mV_px, px_mm = None, None, None
                        sig_mV, time_ms = "[]", "[]"
                        p_pks, q_pks, r_pks, s_pks, t_pks = "[]", "[]", "[]", "[]", "[]"
                        hr, rr_ms, pr, qrs, qt = None, "[]", None, None, None

                        if anal_json:
                            try:
                                anal = json.loads(anal_json)
                                if isinstance(anal, dict):
                                    ms_px = anal.get("ms_per_pixel")
                                    mV_px = anal.get("mV_per_pixel")
                                    px_mm = anal.get("pixels_per_mm")
                                    sig_mV = json.dumps(anal.get("signal_mV", []))
                                    time_ms = json.dumps(anal.get("time_ms", []))

                                    features = anal.get("features", {})
                                    if isinstance(features, dict):
                                        p_pks = json.dumps(features.get("p_peaks", []))
                                        q_pks = json.dumps(features.get("q_peaks", []))
                                        r_pks = json.dumps(features.get("r_peaks", []))
                                        s_pks = json.dumps(features.get("s_peaks", []))
                                        t_pks = json.dumps(features.get("t_peaks", []))

                                    metrics = anal.get("metrics", {})
                                    if isinstance(metrics, dict):
                                        hr = metrics.get("heart_rate_bpm")
                                        rr_ms = json.dumps(metrics.get("rr_intervals_ms", []))
                                        pr = metrics.get("pr_interval_ms")
                                        qrs = metrics.get("qrs_duration_ms")
                                        qt = metrics.get("qt_interval_ms")
                            except Exception:
                                pass

                        conn.execute(
                            """
                            INSERT INTO ecg_records (
                                id, patient_id, ecg_datetime, root_cause, root_cause_time,
                                image_filename, image_hash, ms_per_pixel, mV_per_pixel, pixels_per_mm,
                                signal_mV, time_ms, p_peaks, q_peaks, r_peaks, s_peaks, t_peaks,
                                heart_rate_bpm, rr_intervals_ms, pr_interval_ms, qrs_duration_ms, qt_interval_ms,
                                created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                rec_id, p_id, ecg_dt, rc, rc_time, img_fn, img_hash, ms_px, mV_px, px_mm,
                                sig_mV, time_ms, p_pks, q_pks, r_pks, s_pks, t_pks,
                                hr, rr_ms, pr, qrs, qt, created
                            )
                        )

                    conn.execute("DROP TABLE ecg_records_old")

                # 2. Migrate ecg_comparisons if needed
                cursor = conn.execute("PRAGMA table_info(ecg_comparisons)")
                has_delta_json = any(row[1] == "delta_json" for row in cursor.fetchall())
                if has_delta_json:
                    conn.execute("ALTER TABLE ecg_comparisons RENAME TO ecg_comparisons_old")
                    conn.execute(
                        """
                        CREATE TABLE ecg_comparisons (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            record_a_id INTEGER NOT NULL,
                            record_b_id INTEGER NOT NULL,
                            alignment_method TEXT,
                            heart_rate_bpm REAL,
                            pr_interval_ms REAL,
                            qrs_duration_ms REAL,
                            qt_interval_ms REAL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY(record_a_id) REFERENCES ecg_records(id) ON DELETE CASCADE,
                            FOREIGN KEY(record_b_id) REFERENCES ecg_records(id) ON DELETE CASCADE
                        )
                        """
                    )

                    comp_cursor = conn.execute("SELECT id, record_a_id, record_b_id, alignment_method, delta_json, created_at FROM ecg_comparisons_old")
                    for row in comp_cursor.fetchall():
                        c_id, rec_a, rec_b, align_method, d_json, created = row

                        hr, pr, qrs, qt = None, None, None, None
                        if d_json:
                            try:
                                delta = json.loads(d_json)
                                if isinstance(delta, dict):
                                    metrics = delta.get("delta_metrics") if "delta_metrics" in delta else delta
                                    if isinstance(metrics, dict):
                                        hr = metrics.get("heart_rate_bpm")
                                        pr = metrics.get("pr_interval_ms")
                                        qrs = metrics.get("qrs_duration_ms")
                                        qt = metrics.get("qt_interval_ms")
                            except Exception:
                                pass

                        conn.execute(
                            """
                            INSERT INTO ecg_comparisons (
                                id, record_a_id, record_b_id, alignment_method,
                                heart_rate_bpm, pr_interval_ms, qrs_duration_ms, qt_interval_ms,
                                created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (c_id, rec_a, rec_b, align_method, hr, pr, qrs, qt, created)
                        )

                    conn.execute("DROP TABLE ecg_comparisons_old")
                self.set_db_schema_version(conn, 3)
            except sqlite3.Error as e:
                raise RuntimeError(f"Failed to migrate to schema version 3: {e}")

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
            "user_management_enabled": "true",
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
        import secrets
        from auth import hash_password
        password = "admin"
        salt = secrets.token_hex(16)
        password_hash = hash_password(password, salt)
        now = datetime.now(timezone.utc).isoformat()
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
                    ms_per_pixel REAL,
                    mV_per_pixel REAL,
                    pixels_per_mm REAL,
                    signal_mV TEXT,
                    time_ms TEXT,
                    p_peaks TEXT,
                    q_peaks TEXT,
                    r_peaks TEXT,
                    s_peaks TEXT,
                    t_peaks TEXT,
                    heart_rate_bpm REAL,
                    rr_intervals_ms TEXT,
                    pr_interval_ms REAL,
                    qrs_duration_ms REAL,
                    qt_interval_ms REAL,
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
                    record_a_id INTEGER NOT NULL,
                    record_b_id INTEGER NOT NULL,
                    alignment_method TEXT,
                    heart_rate_bpm REAL,
                    pr_interval_ms REAL,
                    qrs_duration_ms REAL,
                    qt_interval_ms REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(record_a_id) REFERENCES ecg_records(id) ON DELETE CASCADE,
                    FOREIGN KEY(record_b_id) REFERENCES ecg_records(id) ON DELETE CASCADE
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
            conn.commit()

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
        #
        # This method does not assume exclusive ownership of the image
        # directory and therefore only writes the file when it does not
        # already exist (deduplication by content hash). The filename is
        # derived from the high-entropy SHA-256 hash of the bytes which
        # reduces the chance of collisions and simplifies garbage
        # collection when records are deleted.
        self.ensure_storage()
        image_hash = self.compute_hash(image_bytes)
        filename = f"{image_hash[:16]}{ext}"
        path = os.path.join(self._paths.image_dir, filename)
        # Only write when missing to avoid race conditions and repeated I/O.
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(image_bytes)
        return filename

    def load_records(self) -> pd.DataFrame:
        # Load summarized record list for the Records tab. Returns a
        # pandas.DataFrame suitable for direct display in Streamlit. The
        # SQL is intentionally narrow (only summary columns) to keep the
        # result fast and compact for the UI.
        try:
            with sqlite3.connect(self._paths.db_path) as conn:
                return pd.read_sql_query(
                    "SELECT id, patient_id, ecg_datetime, created_at FROM ecg_records ORDER BY created_at DESC",
                    conn,
                )
        except sqlite3.Error as e:
            # Wrap lower-level DB errors in a RuntimeError so callers have
            # a consistent exception type to handle in the UI.
            raise RuntimeError(f"Failed to load records: {e}")

    def load_record(self, record_id: int) -> dict:
        # Fetch a single record with its stored analysis payload.
        try:
            with sqlite3.connect(self._paths.db_path) as conn:
                cursor = conn.execute(
                    "SELECT * FROM ecg_records WHERE id = ?",
                    (record_id,),
                )
                row = cursor.fetchone()
            if not row:
                return {}
            columns = [description[0] for description in cursor.description]
            data = dict(zip(columns, row))
            
            # Reconstruct the nested analysis dictionary structure for compatibility
            analysis = {
                "ms_per_pixel": data.get("ms_per_pixel"),
                "mV_per_pixel": data.get("mV_per_pixel"),
                "pixels_per_mm": data.get("pixels_per_mm"),
                "signal_mV": json.loads(data.get("signal_mV")) if data.get("signal_mV") else [],
                "time_ms": json.loads(data.get("time_ms")) if data.get("time_ms") else [],
                "features": {
                    "p_peaks": json.loads(data.get("p_peaks")) if data.get("p_peaks") else [],
                    "q_peaks": json.loads(data.get("q_peaks")) if data.get("q_peaks") else [],
                    "r_peaks": json.loads(data.get("r_peaks")) if data.get("r_peaks") else [],
                    "s_peaks": json.loads(data.get("s_peaks")) if data.get("s_peaks") else [],
                    "t_peaks": json.loads(data.get("t_peaks")) if data.get("t_peaks") else []
                },
                "metrics": {
                    "heart_rate_bpm": data.get("heart_rate_bpm"),
                    "rr_intervals_ms": json.loads(data.get("rr_intervals_ms")) if data.get("rr_intervals_ms") else [],
                    "pr_interval_ms": data.get("pr_interval_ms"),
                    "qrs_duration_ms": data.get("qrs_duration_ms"),
                    "qt_interval_ms": data.get("qt_interval_ms")
                }
            }
            data["analysis"] = analysis
            # Keep a serialized analysis_json representation in data if any code specifically looks for it
            data["analysis_json"] = json.dumps(analysis)
            return data
        except (sqlite3.Error, json.JSONDecodeError) as e:
            raise RuntimeError(f"Failed to load record {record_id}: {e}")

    def save_record(self, metadata: dict, image_bytes: bytes, ext: str, analysis: dict) -> int:
        # Persist metadata, image, and analysis results in a single row.
        try:
            image_filename = self.save_image_bytes(image_bytes, ext)
            image_hash = self.compute_hash(image_bytes)
            created_at = datetime.now(timezone.utc).isoformat()

            # Unpack analysis dictionary
            features = analysis.get("features", {})
            metrics = analysis.get("metrics", {})

            with sqlite3.connect(self._paths.db_path) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO ecg_records (
                        patient_id, ecg_datetime, root_cause, root_cause_time,
                        image_filename, image_hash, ms_per_pixel, mV_per_pixel, pixels_per_mm,
                        signal_mV, time_ms, p_peaks, q_peaks, r_peaks, s_peaks, t_peaks,
                        heart_rate_bpm, rr_intervals_ms, pr_interval_ms, qrs_duration_ms, qt_interval_ms,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metadata.get("patient_id"),
                        metadata.get("ecg_datetime"),
                        metadata.get("root_cause"),
                        metadata.get("root_cause_time"),
                        image_filename,
                        image_hash,
                        analysis.get("ms_per_pixel"),
                        analysis.get("mV_per_pixel"),
                        analysis.get("pixels_per_mm"),
                        json.dumps(analysis.get("signal_mV", [])),
                        json.dumps(analysis.get("time_ms", [])),
                        json.dumps(features.get("p_peaks", [])),
                        json.dumps(features.get("q_peaks", [])),
                        json.dumps(features.get("r_peaks", [])),
                        json.dumps(features.get("s_peaks", [])),
                        json.dumps(features.get("t_peaks", [])),
                        metrics.get("heart_rate_bpm"),
                        json.dumps(metrics.get("rr_intervals_ms", [])),
                        metrics.get("pr_interval_ms"),
                        metrics.get("qrs_duration_ms"),
                        metrics.get("qt_interval_ms"),
                        created_at,
                    ),
                )
                return cur.lastrowid
        except (sqlite3.Error, OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Failed to save record: {e}")

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

        return True




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
