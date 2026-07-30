#!/usr/bin/env python
"""Quick functionality smoke tests for ECGComparisonPython.

This script provides lightweight sanity checks that can be executed
manually during development to validate database initialization, analysis
pipeline execution, and export formatting without running the full test
suite.
"""

import ECGComparisonPython as app
import numpy as np
from PIL import Image
from analyzer import build_analysis
import data_export

def test_database_init():
    """Test database initialization"""
    try:
        app.init_db()
        print('[OK] Database initialization successful')
        return True
    except Exception as e:
        print(f'[FAIL] Database init error: {e}')
        return False


def test_analysis():
    """Test ECG analysis pipeline"""
    try:
        # Create synthetic image
        img = Image.new('RGB', (800, 300), color='white')
        pixels = img.load()

        # Draw simple wave
        for x in range(800):
            y = 150 + int(20 * np.sin(x / 50))
            for dy in range(-2, 3):
                if 0 <= y + dy < 300:
                    pixels[x, y + dy] = (0, 0, 0)

        # Run analysis
        analysis = build_analysis(img, pixels_per_mm=20.0, prominence_factor=0.5)
        r_peak_count = len(analysis['features']['r_peaks'])
        print(f'[OK] Analysis successful: found {r_peak_count} R-peaks')
        return True
    except Exception as e:
        print(f'[FAIL] Analysis error: {e}')
        import traceback
        traceback.print_exc()
        return False


def test_export():
    """Test export functionality"""
    try:
        metrics = {
            'heart_rate_bpm': 72.0,
            'pr_interval_ms': 160.0,
            'qrs_duration_ms': 90.0,
            'qt_interval_ms': 380.0,
        }
        df = app.metrics_table(metrics)
        print('[OK] Export metrics_table working')
        return True
    except Exception as e:
        print(f'[FAIL] Export error: {e}')
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print('Testing ECGComparisonPython functionality...\n')

    results = [
        test_database_init(),
        test_analysis(),
        test_export(),
    ]

    print(f'\n{"="*50}')
    if all(results):
        print('[SUCCESS] All core functionality verified!')
    else:
        print('[FAILED] Some tests failed')
        exit(1)
