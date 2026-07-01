# ECG Graph Extraction and Analysis System

A comprehensive system for ECG image preprocessing, digitization, feature extraction, comparison, and storage. Originally built as a Streamlit GUI application, it now also features a FastAPI backend and a React Native mobile application for mobile-friendly access to the core analysis pipeline.

## System Architecture

The project is structured into three main components:

1. **Core Application & Web UI (`ECGComparisonPython.py`)**
   - Implements the core domain logic: `ECGDatabase`, `ECGAnalyzer`, `ECGAligner`, and `ECGExporter`.
   - Provides a Streamlit-based web interface for clinicians and researchers.
   - Features include image upload (PNG/JPG/PDF), preprocessing, gridline-based calibration, waveform digitization, R-peak detection, and feature extraction (P, Q, R, S, T).
   - Allows side-by-side comparison of two ECG signals with delta visualization.
   - Supports exporting analysis and comparison outputs to CSV and JSON formats.

2. **Mobile Backend (`mobile_backend/main.py`)**
   - A lightweight FastAPI server that exposes the core Python analysis and persistence functions via a RESTful API.
   - Designed to be consumed by the companion mobile application.
   - Features token-based prototype authentication (HMAC-signed payload) for secure access.
   - Returns base64-encoded matplotlib figures for image plotting to avoid relying on a file server.

3. **Mobile Application (`mobile_app/`)**
   - A React Native (Expo) companion app with a native Android build.
   - Provides screens for Login, ECG Analysis, viewing Records, Comparing ECGs, and Admin Analysis.
   - Communicates with the FastAPI backend to leverage the core Python logic without duplication.
   - Builds a standalone Android APK via Gradle (`mobile_app/android/`).

## Features

- **ECG Processing Pipeline**: Denoise, contrast enhancement, grid detection (px/mm), waveform digitization, and signal conversion (mV).
- **Clinical Metrics**: Automatic computation of Heart Rate (bpm), RR intervals, PR interval, QRS duration, and QT interval.
- **Comparison Engine**: Align two ECG signals using R-peak alignment or cross-correlation, compute delta waveforms, and comparison metrics.
- **Data Persistence**: Uses a local SQLite database (`data/ecg.db`) to store user records, analysis JSON blobs, audit logs, and settings. Original images are deduplicated via SHA-256 hashes and stored locally.
- **User Management & RBAC**: Role-Based Access Control with local token auth, auditing, and configurable privacy restrictions for patient data.

## Requirements

### Python Core & Web App
- Windows / Linux
- Python 3.10+
- See `requirements.txt` for Python dependencies (e.g., Streamlit, OpenCV, NumPy, SciPy, Matplotlib, Pandas).

### Mobile App
- Node.js & npm/yarn
- Expo CLI

### Android Build
- JDK 17+ (OpenJDK 21 recommended; ships with Android Studio)
- Android SDK (API level 36 / build-tools 36.0.0)
- Android NDK 27.1.12297006 (installed automatically by the build)
- `JAVA_HOME` and `ANDROID_HOME` environment variables, or `local.properties` in `mobile_app/android/` with `sdk.dir` set

## Setup & Running

### 1. Web Application (Streamlit)
1. Create and activate a Python virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the Streamlit app:
   ```bash
   python -m streamlit run ECGComparisonPython.py
   ```
   *(Alternatively, use the provided `start_server.bat` / `StartServer.bat` on Windows)*

### 2. Mobile Backend (FastAPI)
1. Ensure the Python virtual environment is activated and dependencies are installed.
2. Run the FastAPI server:
   ```bash
   python mobile_backend/main.py
   ```
   *The server runs on port 8000 by default.*

### 3. Mobile App (React Native)
1. Navigate to the `mobile_app` directory:
   ```bash
   cd mobile_app
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the Expo development server:
   ```bash
   npx expo start
   ```

### 4. Android APK Build
1. Ensure `JAVA_HOME` points to a JDK 17+ installation and `ANDROID_HOME` (or `sdk.dir` in `mobile_app/android/local.properties`) points to the Android SDK:
   ```properties
   # mobile_app/android/local.properties
   sdk.dir=C:/Users/<YOUR_USER>/AppData/Local/Android/Sdk
   ```
2. Install JS dependencies (if not already done):
   ```bash
   cd mobile_app
   npm install
   ```
3. Build the debug APK:
   ```bash
   cd android
   gradlew.bat assembleDebug        # Windows
   ./gradlew assembleDebug           # Linux / macOS
   ```
4. The output APK will be at:
   ```
   mobile_app/android/app/build/outputs/apk/debug/app-debug.apk
   ```
5. Install on a connected device or emulator:
   ```bash
   adb install app/build/outputs/apk/debug/app-debug.apk
   ```

> **Note:** The mobile app connects to the FastAPI backend at `http://10.0.2.2:8000` (the Android emulator's alias for the host's localhost). This is configured in `mobile_app/config.js`. Make sure the mobile backend is running before launching the app.

## Data Storage
- **Database**: `data/ecg.db`
- **Images**: `data/images/`

## Disclaimer
Automatic grid detection accuracy heavily depends on image quality and grid visibility. This system is intended as an MVP tool for research and workflow simplification; it is **not** a clinical-grade diagnostic tool.
