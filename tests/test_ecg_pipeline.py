import io
import json
import os
import numpy as np
from PIL import Image

import ECGComparisonPython as app


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


def test_compute_hash_consistency():
    data = b"sample-bytes"
    assert app.compute_hash(data) == app.compute_hash(data)


def test_waveform_to_signal_baseline_centering():
    y_pixels = np.array([10, 12, 14, 16], dtype=float)
    signal = app.waveform_to_signal(y_pixels, mV_per_pixel=0.1)
    # Median is 13, so values should be centered around zero.
    assert np.isclose(signal.mean(), 0.0, atol=1e-6)


def test_detect_r_peaks_basic():
    # Build a simple signal with clear peaks.
    signal = np.zeros(500)
    signal[100] = 1.0
    signal[300] = 1.2
    peaks = app.detect_r_peaks(signal, ms_per_pixel=2.0)
    assert 100 in peaks and 300 in peaks


def test_build_analysis_output_shapes():
    image = _synthetic_ecg_image()
    analysis = app.build_analysis(image, pixels_per_mm=20.0, prominence_factor=0.5)
    assert "signal_mV" in analysis
    assert "time_ms" in analysis
    assert len(analysis["signal_mV"]) == len(analysis["time_ms"])


def test_metrics_table_structure():
    metrics = {
        "heart_rate_bpm": 70.0,
        "pr_interval_ms": 160.0,
        "qrs_duration_ms": 90.0,
        "qt_interval_ms": 380.0,
    }
    df = app.metrics_table(metrics)
    assert list(df.columns) == ["Metric", "Value"]
    assert len(df) == 4


def test_align_signals_r_peak():
    sig_a = np.zeros(100)
    sig_b = np.zeros(100)
    sig_a[10] = 1.0
    sig_b[15] = 1.0
    aligned_a, aligned_b, method = app.align_signals(sig_a, sig_b, [10], [15])
    assert method == "r-peak"
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
    csv_data, json_data = app.analysis_to_exports(analysis)
    assert "Heart Rate" in csv_data
    parsed = json.loads(json_data)
    assert parsed["metrics"]["heart_rate_bpm"] == 70.0


def test_save_and_load_record_roundtrip(tmp_path, monkeypatch):
    data_dir = tmp_path
    image_dir = tmp_path / "images"
    db_path = tmp_path / "ecg.db"

    monkeypatch.setattr(app, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(app, "IMAGE_DIR", str(image_dir))
    monkeypatch.setattr(app, "DB_PATH", str(db_path))

    app.init_db()

    image = _synthetic_ecg_image()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    metadata = {
        "patient_id": "P-100",
        "ecg_datetime": "2026-02-02 10:00",
        "root_cause": "stress",
        "root_cause_time": "09:45",
    }
    analysis = {"metrics": {"heart_rate_bpm": 72.0}}
    record_id = app.save_record(metadata, image_bytes, ".png", analysis)

    records = app.load_records()
    assert record_id in records["id"].tolist()

    record = app.load_record(record_id)
    assert record["patient_id"] == "P-100"
    assert record["analysis"]["metrics"]["heart_rate_bpm"] == 72.0
    assert os.path.exists(os.path.join(app.IMAGE_DIR, record["image_filename"]))
