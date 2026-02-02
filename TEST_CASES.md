# Test Cases — ECG Graph Extraction and Analysis System

## Unit Test Case Plan
This section is a concrete unit-test plan for the refactored OO core in ECGComparisonPython.py:

- Core classes: `StoragePaths`, `ECGDatabase`, `ECGAnalyzer`, `ECGAligner`, `ECGExporter`
- Facade: module-level functions (e.g., `init_db`, `build_analysis`, `save_record`) which delegate to the classes

### Test Strategy
- Prefer unit tests that avoid Streamlit UI interactions.
- Use `tmp_path` + `monkeypatch` to isolate filesystem/SQLite state (as already done in `tests/test_ecg_pipeline.py`).
- For image-based tests, prefer small synthetic images to keep runtime predictable.
- Keep algorithmic tests tolerant (use approximate assertions) since image digitization is heuristic.

### Fixtures / Helpers
- `tmp_path` for isolated `DATA_DIR`, `IMAGE_DIR`, `DB_PATH`.
- `_synthetic_ecg_image()` helper (already present) for deterministic waveforms.
- Consider adding a `make_png_bytes(image)` helper to reduce duplication.

### Planned Unit Tests (Detailed)

#### 1) Storage + Database (`ECGDatabase`)
- UT-DB-01: `ensure_storage()` creates `image_dir` when missing.
- UT-DB-02: `init_db()` creates expected tables (`ecg_records`, `ecg_comparisons`, `users`, `audit_logs`, `app_settings`).
- UT-DB-03: `get_setting()` returns default when key missing; returns persisted value when key exists.
- UT-DB-04: `set_setting()` upserts (insert then update) the same key.
- UT-DB-05: `compute_hash()` stable for same bytes; different for different bytes.

##### Persistence of images/records
- UT-DB-06: `save_image_bytes()` writes file once (same bytes returns same filename; file not duplicated).
- UT-DB-07: `save_record()` inserts row with expected columns populated, and returns an integer ID.
- UT-DB-08: `load_records()` returns a DataFrame with expected columns and includes inserted ID.
- UT-DB-09: `load_record()` returns `{}` for missing ID; returns dict with parsed `analysis` for existing ID.

##### Deletion semantics
- UT-DB-10: `delete_record()` returns False for missing record.
- UT-DB-11: `delete_record()` deletes DB row and deletes image file if no other record references it.
- UT-DB-12: `delete_record()` does NOT delete image file if another record references the same `image_filename`.
	- Setup: save the same image bytes twice with two records; delete one; ensure image remains; delete second; ensure image removed.

##### Migrations / schema version
- UT-DB-13: `migrate_db()` updates schema version in `app_settings`.
- UT-DB-14: `ecg_comparisons_has_foreign_keys()` returns True for a freshly initialized DB.
	- Optional: create a minimal legacy table without FK and verify migration rebuilds table (heavier test; consider marking slow).

#### 2) Analysis Pipeline (`ECGAnalyzer`)
- UT-AN-01: `preprocess_image()` returns dict containing `gray` and `enhanced` with same width/height as input.
- UT-AN-02: `detect_grid_spacing()` returns `None` for images without gridlines (e.g., constant background).
- UT-AN-03: `digitize_waveform()` returns an array with length == image width.
- UT-AN-04: `waveform_to_signal()` baseline-centers signal around 0 (mean close to 0 for symmetric input).
- UT-AN-05: `detect_r_peaks()` finds peaks in a simple synthetic 1D signal.
- UT-AN-06: `extract_features()` returns lists of indices and preserves `r_peaks`.
- UT-AN-07: `compute_metrics()` handles <2 R-peaks (heart rate None) and >=2 R-peaks (heart rate finite).
- UT-AN-08: `build_analysis()` returns required keys and consistent array sizes (`signal_mV` length matches `time_ms`).

#### 3) Alignment (`ECGAligner`)
- UT-AL-01: `align_signals()` chooses `r-peak` method when both R-peak lists are non-empty.
- UT-AL-02: `align_signals()` chooses `cross-correlation` when either list is empty.
- UT-AL-03: Returned aligned arrays have equal length and are <= original lengths.
- UT-AL-04: Zero shift case keeps arrays unchanged (except cropping to min length).

#### 4) Exports (`ECGExporter`)
- UT-EX-01: `metrics_table()` returns DataFrame with columns `[Metric, Value]` and expected row count.
- UT-EX-02: `analysis_to_exports()` returns CSV containing human labels and JSON parseable into a dict.
- UT-EX-03: `analysis_to_exports()` preserves numeric types where expected (e.g., `heart_rate_bpm`).

#### 5) Facade Contract (module-level functions)
Goal: ensure backward compatibility even though implementation is OO.

- UT-API-01: Facade `build_analysis()` matches `ECGAnalyzer().build_analysis()` output schema (keys present and lengths match).
- UT-API-02: Facade DB functions (`init_db`, `save_record`, `load_record`, `delete_record`) behave correctly under monkeypatched paths.
- UT-API-03: Facade functions are deterministic for deterministic inputs (hash, peak detection on constructed signals).

#### 6) User Management + Audit (unit-level, not UI)
These can be unit-tested by calling the DB helpers directly (without Streamlit), but require some careful setup.

- UT-AUTH-01: `hash_password()` + `verify_password()` roundtrip.
- UT-AUTH-02: `create_user()` inserts user; `get_user_by_username()` retrieves it.
- UT-AUTH-03: `authenticate_user()` returns user for correct password; returns None for wrong password; returns None for disabled user.
- UT-AUD-01: `log_audit()` inserts a row; `list_audit_logs()` returns most recent entries.

### Mapping to Existing Automated Tests
The following are already covered in `tests/test_ecg_pipeline.py`:
- `compute_hash` consistency
- baseline centering via `waveform_to_signal`
- basic R-peak detection
- `build_analysis` output shape checks
- metrics table structure
- `align_signals` with R-peak alignment
- export formatting
- record save/load/delete roundtrip (with `tmp_path` + monkeypatch)

### Priority / Execution Order
1. UT-DB-* and UT-API-* (data integrity and backwards compatibility)
2. UT-AN-* (signal extraction and metrics)
3. UT-AL-* and UT-EX-*
4. UT-AUTH-* / UT-AUD-* (if user management enabled in your environment)

## 1. Analysis & Export
- TC-AN-01: Upload a valid ECG image (PNG/JPG/PDF) and run analysis; verify waveform plot and metrics table render.
- TC-AN-02: Download CSV export; verify the file contains metric labels and numeric values.
- TC-AN-03: Download JSON export; verify structure includes `metrics` and `features` objects.

## 2. Records
- TC-REC-01: Save a record after analysis; verify record appears in Records tab with ID, patient ID, and ECG date/time.
- TC-REC-02: Re-open app and verify records persist.

## 3. Comparison
- TC-CMP-01: Compare two records; verify alignment method is shown and delta table renders.
- TC-CMP-02: Compare one record and one uploaded ECG; verify comparison completes and exports are available.
- TC-CMP-03: Compare two uploads; verify alignment and delta plots render.

## 4. User Management (When Enabled)
- TC-UM-01: Login required for access to Analyze, Compare, Records, and Exports.
- TC-UM-02: Administrator can create a user and assign a role.
- TC-UM-03: Administrator can disable a user; disabled user cannot log in.
- TC-UM-04: Clinician can run analysis and export results.
- TC-UM-05: Researcher access to patient identifiers is restricted when configured.
- TC-UM-06: Audit logs record login success/failure, logout, session timeout, and record export.
- TC-UM-07: Only Administrator can view audit logs.
