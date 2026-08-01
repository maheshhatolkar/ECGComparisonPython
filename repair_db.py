import sqlite3
import os
import numpy as np
from PIL import Image
import db
from analyzer import ECGAnalyzer

def repair():
    paths = db.StoragePaths.current()
    analyzer = ECGAnalyzer()
    
    with sqlite3.connect(paths.db_path) as conn:
        cursor = conn.execute("""
            SELECT r.id, r.image_filename, r.pixels_per_mm
            FROM ecg_records r
            LEFT JOIN ecg_signals s ON r.id = s.record_id
            WHERE s.id IS NULL
        """)
        missing_records = cursor.fetchall()
        
        print(f"Found {len(missing_records)} records missing signals.")
        
        for rec_id, img_file, px_mm in missing_records:
            print(f"Repairing record {rec_id} using image {img_file}")
            img_path = os.path.join(paths.image_dir, img_file)
            if not os.path.exists(img_path):
                print(f"Image {img_path} not found for record {rec_id}. Skipping.")
                continue
                
            try:
                img = Image.open(img_path).convert("RGB")
                img_array = np.array(img)
                
                # Use standard fallback if pixels_per_mm is None or 0
                if not px_mm or px_mm <= 0:
                    px_mm = 68.0
                    
                analysis = analyzer.build_analysis(img, pixels_per_mm=px_mm, prominence_factor=0.5)
                
                # Update ecg_records
                metrics = analysis.get("metrics", {})
                conn.execute("""
                    UPDATE ecg_records SET
                        ms_per_pixel = ?,
                        mV_per_pixel = ?,
                        pixels_per_mm = ?,
                        heart_rate_bpm = ?,
                        pr_interval_ms = ?,
                        qrs_duration_ms = ?,
                        qt_interval_ms = ?
                    WHERE id = ?
                """, (
                    analysis.get("ms_per_pixel"),
                    analysis.get("mV_per_pixel"),
                    analysis.get("pixels_per_mm"),
                    metrics.get("heart_rate_bpm"),
                    metrics.get("pr_interval_ms"),
                    metrics.get("qrs_duration_ms"),
                    metrics.get("qt_interval_ms"),
                    rec_id
                ))
                
                # Insert signals
                signal_mv = analysis.get("signal_mV", [])
                time_ms = analysis.get("time_ms", [])
                if signal_mv and time_ms and len(signal_mv) == len(time_ms):
                    conn.executemany(
                        "INSERT INTO ecg_signals (record_id, time_ms, signal_mV) VALUES (?, ?, ?)",
                        [(rec_id, float(t), float(s)) for t, s in zip(time_ms, signal_mv)]
                    )
                
                # Insert peaks
                features = analysis.get("features", {})
                peaks_data = []
                for p_type, col_name in [("P", "p_peaks"), ("Q", "q_peaks"), ("R", "r_peaks"), ("S", "s_peaks"), ("T", "t_peaks")]:
                    peaks = features.get(col_name, [])
                    if peaks:
                        peaks_data.extend([(rec_id, p_type, float(t)) for t in peaks])
                if peaks_data:
                    conn.executemany(
                        "INSERT INTO ecg_peaks (record_id, peak_type, time_ms) VALUES (?, ?, ?)",
                        peaks_data
                    )
                
                # Insert RR intervals
                rr = metrics.get("rr_intervals_ms", [])
                if rr:
                    conn.executemany(
                        "INSERT INTO ecg_rr_intervals (record_id, sequence_order, duration_ms) VALUES (?, ?, ?)",
                        [(rec_id, i, float(val)) for i, val in enumerate(rr)]
                    )
                
            except Exception as e:
                print(f"Failed to repair record {rec_id}: {e}")
                
        conn.commit()
        print("Done.")

if __name__ == "__main__":
    repair()
