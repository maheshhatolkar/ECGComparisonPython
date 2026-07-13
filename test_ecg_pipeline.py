"""Unit tests covering the ECG analysis pipeline and helper utilities.

These tests exercise typical usage (happy path) as well as error and
edge-case behavior for analysis, alignment, and export helpers. They are
designed to run quickly and provide deterministic coverage for core logic.
"""

import json
import os
import numpy as np
import pytest
from PIL import Image

import analyzer
import db


def _synthetic_ecg_image(width=800, height=300):
    # Build a synthetic ECG-like trace on a light background.
    img = np.full((height, width, 3), 245, dtype=np.uint8)
    mid = height // 2
    for x in range(width):
        # Simple wave: baseline + occasional sharp spikes.
        if x % 200 == 50:
            y = mid - 40
        elif x % 200 == 52:
            y = mid + 20
        else:
            y = int(mid + 5 * np.sin(x / 15))
        img[max(0, y - 1):min(height, y + 2), x] = [0, 0, 0]
    return Image.fromarray(img)


# ============================================================================
# Happy Path Tests
# ============================================================================

def test_compute_hash_consistency():
    data = b"sample-bytes"
    assert db.compute_hash(data) == db.compute_hash(data)


def test_compute_hash_different_data():
    """Hash should differ for different inputs."""
    hash1 = db.compute_hash(b"data1")
    hash2 = db.compute_hash(b"data2")
    assert hash1 != hash2


def test_waveform_to_signal_baseline_centering():
    y_pixels = np.array([10, 12, 14, 16], dtype=float)
    signal = analyzer.waveform_to_signal(y_pixels, mV_per_pixel=0.1)
    # Median is 13, so values should be centered around zero.
    assert np.isclose(signal.mean(), 0.0, atol=1e-6)


def test_waveform_to_signal_scaling():
    """Signal should scale correctly with mV_per_pixel."""
    y_pixels = np.array([100.0, 110.0], dtype=float)
    signal = analyzer.waveform_to_signal(y_pixels, mV_per_pixel=0.1)
    # Difference should be scaled by mV_per_pixel
    assert np.isclose(signal[0] - signal[1], 1.0, atol=1e-6)


def test_detect_r_peaks_basic():
    # Build a simple signal with clear peaks.
    signal = np.zeros(500)
    signal[100] = 1.0
    signal[300] = 1.2
    peaks = analyzer.detect_r_peaks(signal, ms_per_pixel=2.0)
    assert 100 in peaks and 300 in peaks


def test_detect_r_peaks_no_peaks():
    """Detect should handle signal with no peaks."""
    signal = np.ones(100) * 0.5
    peaks = analyzer.detect_r_peaks(signal, ms_per_pixel=2.0)
    assert isinstance(peaks, np.ndarray)


def test_build_analysis_output_shapes():
    image = _synthetic_ecg_image()
    analysis = analyzer.build_analysis(image, pixels_per_mm=20.0, prominence_factor=0.5)
    assert "signal_mV" in analysis
    assert "time_ms" in analysis
    assert len(analysis["signal_mV"]) == len(analysis["time_ms"])
    assert "metrics" in analysis
    assert "features" in analysis


def test_metrics_table_structure():
    metrics = {
        "heart_rate_bpm": 70.0,
        "pr_interval_ms": 160.0,
        "qrs_duration_ms": 90.0,
        "qt_interval_ms": 380.0,
    }
    df = analyzer.metrics_table(metrics)
    assert list(df.columns) == ["Metric", "Value"]
    assert len(df) == 4


def test_align_signals_r_peak():
    sig_a = np.zeros(100)
    sig_b = np.zeros(100)
    sig_a[10] = 1.0
    sig_b[15] = 1.0
    aligned_a, aligned_b, method = analyzer.align_signals(sig_a, sig_b, [10], [15])
    assert method == "r-peak"
    assert len(aligned_a) == len(aligned_b)


def test_align_signals_cross_correlation():
    """Test alignment fallback to cross-correlation."""
    sig_a = np.zeros(100)
    sig_b = np.zeros(100)
    sig_a[50] = 1.0
    sig_b[60] = 1.0
    aligned_a, aligned_b, method = analyzer.align_signals(sig_a, sig_b, [], [])
    assert method == "cross-correlation"
    assert len(aligned_a) == len(aligned_b)


def test_analysis_export_formats():
    analysis = {
        "metrics": {
            "heart_rate_bpm": 70.0,
            "pr_interval_ms": 160.0,
            "qrs_duration_ms": 90.0,
            "qt_interval_ms": 380.0,
        }
    }
    csv_data = analyzer.analysis_to_exports(analysis)
    assert "Heart Rate" in csv_data


# ============================================================================
# Error Scenario Tests
# ============================================================================

def test_build_analysis_invalid_pixels_per_mm():
    """build_analysis should raise error for invalid pixels_per_mm."""
    image = _synthetic_ecg_image()
    with pytest.raises(RuntimeError):
        analyzer.build_analysis(image, pixels_per_mm=0, prominence_factor=0.5)

    with pytest.raises(RuntimeError):
        analyzer.build_analysis(image, pixels_per_mm=-10, prominence_factor=0.5)


def test_build_analysis_invalid_prominence_factor():
    """build_analysis should raise error for invalid prominence_factor."""
    image = _synthetic_ecg_image()
    with pytest.raises(RuntimeError):
        analyzer.build_analysis(image, pixels_per_mm=20.0, prominence_factor=1.5)

    with pytest.raises(RuntimeError):
        analyzer.build_analysis(image, pixels_per_mm=20.0, prominence_factor=-0.1)


def test_waveform_to_signal_empty_array():
    """waveform_to_signal should handle empty arrays."""
    y_pixels = np.array([], dtype=float)
    signal = analyzer.waveform_to_signal(y_pixels, mV_per_pixel=0.1)
    assert len(signal) == 0


def test_metrics_table_missing_metrics():
    """metrics_table should handle missing metric keys gracefully."""
    metrics = {"heart_rate_bpm": 70.0}  # Missing other metrics
    df = analyzer.metrics_table(metrics)
    assert len(df) == 4  # Still creates rows
    assert pd.isna(df.iloc[1]["Value"])  # Missing values are NaN


def test_align_signals_empty_signals():
    """align_signals should handle minimal signal lengths."""
    sig_a = np.array([1.0])
    sig_b = np.array([1.0])
    aligned_a, aligned_b, method = analyzer.align_signals(sig_a, sig_b, [], [])
    assert len(aligned_a) >= 0
    assert len(aligned_b) >= 0
    assert len(aligned_a) == len(aligned_b)


def test_compute_hash_empty_data():
    """compute_hash should handle empty data."""
    hash_val = db.compute_hash(b"")
    assert isinstance(hash_val, str)
    assert len(hash_val) == 64  # SHA-256 produces 64 hex characters
# ============================================================================
# Import pandas for test assertions
# ============================================================================
import pandas as pd


def test_data_export_formatting(tmp_path):
    import io
    import sqlite3
    import data_export
    from db import StoragePaths, ECGDatabase

    # 1. Setup temporary database paths
    temp_data_dir = tmp_path / "data"
    temp_image_dir = temp_data_dir / "images"
    temp_db_path = temp_data_dir / "ecg.db"

    paths = StoragePaths(
        base_dir=str(tmp_path),
        data_dir=str(temp_data_dir),
        image_dir=str(temp_image_dir),
        db_path=str(temp_db_path)
    )

    # Initialize the database schema
    database = ECGDatabase(paths)
    database.init_db()

    # 2. Insert a record with analysis_json
    metadata = {
        "patient_id": "P-EXPORT-TEST",
        "ecg_datetime": "2026-07-14 10:00",
        "root_cause": "test",
        "root_cause_time": "09:45"
    }

    image = Image.new("RGB", (10, 10), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    analysis = {
        "ms_per_pixel": 2.0,
        "mV_per_pixel": 0.05,
        "pixels_per_mm": 20.0,
        "signal_mV": [0.1, 0.2, 0.3],
        "time_ms": [0.0, 2.0, 4.0],
        "features": {
            "r_peaks": [1, 2]
        },
        "metrics": {
            "heart_rate_bpm": 75.0,
            "pr_interval_ms": 150.0
        }
    }

    database.save_record(metadata, image_bytes, ".png", analysis)

    # 3. Read table data using get_table_data and verify
    with sqlite3.connect(paths.db_path) as conn:
        df = data_export.get_table_data(conn, "ecg_records")

    # Verify exclusions
    assert "image_hash" not in df.columns
    assert "image_filename" not in df.columns
    assert "analysis_json" not in df.columns

    # Verify expansions
    assert "ms_per_pixel" in df.columns
    assert "mV_per_pixel" in df.columns
    assert "pixels_per_mm" in df.columns
    assert "signal_mV" in df.columns
    assert "time_ms" in df.columns
    assert "features" in df.columns
    assert "metrics" in df.columns

    # Verify nested metrics
    assert "metrics_heart_rate_bpm" in df.columns
    assert "heart_rate_bpm" in df.columns
    assert df.loc[0, "heart_rate_bpm"] == 75.0

    # Verify nested features
    assert "features_r_peaks" in df.columns
    assert "r_peaks" in df.columns

    # 4. Verify full export runs successfully
    csv_files = data_export.export_all_tables_to_csv(paths=paths)
    assert len(csv_files) > 0

    # Check that ecg_records.csv exists and has correct headers
    record_csv_path = [f for f in csv_files if "ecg_records.csv" in f][0]
    csv_df = pd.read_csv(record_csv_path)
    assert "image_hash" not in csv_df.columns
    assert "heart_rate_bpm" in csv_df.columns

    # Verify excel export
    excel_file = data_export.export_all_tables_to_excel(paths=paths)
    assert excel_file.endswith(".xlsx") or excel_file.endswith(".zip")

