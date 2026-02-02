# ECG Graph Extraction and Analysis System

GUI app for ECG image preprocessing, digitization, feature extraction, comparison, and storage based on SRS.pdf.

## Features
- Upload ECG images (PNG/JPG/PDF)
- Preprocessing (denoise, contrast enhancement)
- Gridline-based calibration (pixels per mm)
- Waveform digitization
- Feature extraction (P, Q, R, S, T) and metrics
- ECG comparison with alignment and delta visualization
- SQLite storage for analyses and images
- Export to CSV and JSON

## Requirements
- Windows/Linux
- Python 3.10+

## Setup
1. Create/activate a virtual environment.
2. Install dependencies:
   - pip install -r requirements.txt

## Run
- Windows (PowerShell):
  - C:/Projects/ECGComparisonPython/.venv/Scripts/python.exe -m streamlit run ECGComparisonPython.py

## Usage
1. Open the app in your browser.
2. Analyze an ECG in the “Analyze” tab.
3. Save to database with metadata.
4. Compare two ECGs in the “Compare” tab.
5. Download CSV/JSON outputs.

## Data Storage
- Database: data/ecg.db
- Images: data/images/

## Notes
- Automatic grid detection may fail on low-quality scans. Use manual pixels-per-mm when needed.
- This is an MVP and not a clinical-grade diagnostic tool.
