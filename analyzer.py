import numpy as np
import json
import pandas as pd
import cv2
from PIL import Image
from scipy.signal import find_peaks, savgol_filter
from scipy.signal import correlate
import functools

class ECGAnalyzer:
    def preprocess_image(self, image: Image.Image) -> dict:
        """Preprocess an RGB PIL Image for waveform extraction.

        This performs conversion to grayscale, denoising and adaptive
        contrast enhancement (CLAHE). Returns a dict with keys:
        - "gray": the raw grayscale image as a numpy array
        - "enhanced": the contrast-enhanced image used by downstream steps

        Raises RuntimeError on failure with an explanatory message.
        """
        # Convert to grayscale and apply denoise + contrast enhancement.
        try:
            rgb = np.array(image)
            if rgb is None or rgb.size == 0:
                raise ValueError("Image array is empty or invalid")
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            denoised = cv2.medianBlur(gray, 3)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(denoised)
            return {
                "gray": gray,
                "enhanced": enhanced,
            }
        except Exception as e:
            raise RuntimeError(f"Image preprocessing failed: {e}")

    def detect_grid_spacing(self, enhanced_gray: np.ndarray) -> float | None:
        """Estimate the median grid line spacing (in pixels) from an enhanced
        grayscale ECG image.

        The algorithm detects prominent horizontal and vertical line peaks and
        returns the median spacing between detected lines. Returns None when
        spacing cannot be determined.
        """
        # Detect gridlines and estimate median spacing between them.
        try:
            h, w = enhanced_gray.shape
            if h < 10 or w < 10:
                return None
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
        except Exception as e:
            print(f"Grid spacing detection failed: {e}")
            return None

    def digitize_waveform(self, enhanced_gray: np.ndarray) -> np.ndarray:
        """Digitize the ECG waveform by tracing darkest/most-contrasting
        pixels across each column of the enhanced grayscale image.

        The function returns a 1-D numpy array of vertical pixel coordinates
        (float) representing the waveform trace, smoothed with a Savitzky-
        Golay filter for noise reduction.
        """
        # Trace the darkest pixel per column to build a waveform.
        try:
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
        except Exception as e:
            raise RuntimeError(f"Waveform digitization failed: {e}")

    def waveform_to_signal(self, y_pixels: np.ndarray, mV_per_pixel: float) -> np.ndarray:
        """Convert waveform pixel coordinates to a millivolt (mV) signal.

        The baseline is estimated as the median y coordinate and subtracted so
        that the resulting signal is centered around 0 mV. Returns a numpy
        array of floats in millivolts.
        """
        # Convert from image coordinates to baseline-centered amplitudes.
        if len(y_pixels) == 0:
            return np.array([])
        baseline = np.median(y_pixels)
        return (baseline - y_pixels) * mV_per_pixel

    def detect_r_peaks(self, signal: np.ndarray, ms_per_pixel: float, prominence_factor: float = 0.5) -> np.ndarray:
        """Detect R-peaks in the ECG signal using scipy.find_peaks.

        The function computes a minimum sample distance derived from an
        expected refractory period (roughly 200 ms) and a prominence based on
        the signal's standard deviation scaled by prominence_factor.
        Returns an array of peak indices.
        """
        # Use distance and prominence heuristics to find R-peaks.
        distance = int(200 / ms_per_pixel)
        distance = max(distance, 1)
        prominence = max(0.05, float(np.std(signal) * prominence_factor))
        peaks, _ = find_peaks(signal, distance=distance, prominence=prominence)
        return peaks

    def extract_features(self, signal: np.ndarray, ms_per_pixel: float, r_peaks: np.ndarray) -> dict:
        """Extract P, Q, R, S, T feature indices around each detected R-peak.

        Windows are defined in milliseconds converted to samples using
        ms_per_pixel. The heuristic locates local extrema around R-peaks to
        approximate the positions of P/Q/S/T wave components. Returns a
        dictionary with lists of indices for each feature.
        """
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
        """Compute clinical metrics (HR, PR/QRS/QT intervals) from features.

        Returns a dictionary containing heart_rate_bpm and interval estimates
        in milliseconds. Where insufficient data exists to compute a metric,
        the value will be None.
        """
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
        """Execute the full image-to-analysis pipeline.

        Steps performed:
        1. Preprocess the image (grayscale, denoise, contrast).
        2. Digitize the waveform trace.
        3. Convert pixels to millivolts and compute timing (ms per sample).
        4. Detect R-peaks and extract waveform features.
        5. Compute summary metrics and package the results into a dict
           containing signal_mV, time_ms, features and metrics.

        Raises RuntimeError on failure and returns a serializable dict on
        success suitable for UI display or export.
        """
        # Run full pipeline: preprocess -> digitize -> detect peaks -> metrics.
        try:
            if pixels_per_mm <= 0:
                raise ValueError("pixels_per_mm must be positive")
            if prominence_factor < 0 or prominence_factor > 1:
                raise ValueError("prominence_factor must be between 0 and 1")

            prep = self.preprocess_image(image)
            waveform_pixels = self.digitize_waveform(prep["enhanced"])
            ms_per_pixel = 40 / pixels_per_mm
            mV_per_pixel = 0.1 / pixels_per_mm
            signal = self.waveform_to_signal(waveform_pixels, mV_per_pixel)
            r_peaks = self.detect_r_peaks(signal, ms_per_pixel, prominence_factor)
            features = self.extract_features(signal, ms_per_pixel, r_peaks)
            metrics = self.compute_metrics(features, ms_per_pixel)
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
        except Exception as e:
            raise RuntimeError(f"ECG analysis pipeline failed: {e}")


class ECGAligner:
    def align_signals(self, signal_a: np.ndarray, signal_b: np.ndarray, r_a: list, r_b: list) -> tuple:
        """Align two ECG signals either by R-peak alignment or cross-correlation.

        Returns a tuple (aligned_a, aligned_b, method) where aligned_* are
        numpy arrays cropped to the same length and method is the alignment
        technique used ("r-peak" or "cross-correlation").
        """
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
        """Format a metrics dictionary into a pandas DataFrame for display.

        Produces two columns: 'Metric' and 'Value', suitable for rendering in
        tables or converting to CSV for export.
        """
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
        """Create CSV and pretty-printed JSON representations of analysis.

        Returns a tuple (csv_string, json_string) which callers can send as
        file downloads or attach to exported reports.
        """
        # Build CSV and JSON payloads for downloads.
        csv_df = self.metrics_table(analysis["metrics"])
        csv_data = csv_df.to_csv(index=False)
        json_data = json.dumps(analysis, indent=2)
        return csv_data, json_data


def _db() -> ECGDatabase:
    # Lazy wrapper for the database layer.
    return ECGDatabase(StoragePaths.current())



@functools.lru_cache(maxsize=1)
def get_analyzer() -> ECGAnalyzer:
    return ECGAnalyzer()

@functools.lru_cache(maxsize=1)
def get_aligner() -> ECGAligner:
    return ECGAligner()

@functools.lru_cache(maxsize=1)
def get_exporter() -> ECGExporter:
    return ECGExporter()

def preprocess_image(image: Image.Image) -> dict:
    return get_analyzer().preprocess_image(image)

def detect_grid_spacing(enhanced_gray: np.ndarray) -> float | None:
    return get_analyzer().detect_grid_spacing(enhanced_gray)

def digitize_waveform(enhanced_gray: np.ndarray) -> np.ndarray:
    return get_analyzer().digitize_waveform(enhanced_gray)

def extract_features(signal: np.ndarray, ms_per_pixel: float, r_peaks: np.ndarray) -> dict:
    return get_analyzer().extract_features(signal, ms_per_pixel, r_peaks)

def compute_metrics(features: dict, ms_per_pixel: float) -> dict:
    return get_analyzer().compute_metrics(features, ms_per_pixel)

def waveform_to_signal(y_pixels: np.ndarray, mV_per_pixel: float) -> np.ndarray:
    return get_analyzer().waveform_to_signal(y_pixels, mV_per_pixel)

def detect_r_peaks(signal: np.ndarray, ms_per_pixel: float, prominence_factor: float = 0.5) -> np.ndarray:
    return get_analyzer().detect_r_peaks(signal, ms_per_pixel, prominence_factor)

def build_analysis(image: Image.Image, pixels_per_mm: float, prominence_factor: float = 0.5) -> dict:
    return get_analyzer().build_analysis(image, pixels_per_mm, prominence_factor)

def metrics_table(metrics: dict) -> pd.DataFrame:
    return get_exporter().metrics_table(metrics)

def align_signals(signal_a: np.ndarray, signal_b: np.ndarray, r_a: list, r_b: list) -> tuple:
    return get_aligner().align_signals(signal_a, signal_b, r_a, r_b)

def analysis_to_exports(analysis: dict) -> tuple[str, str]:
    return get_exporter().analysis_to_exports(analysis)

def comparison_metrics(metrics_a: dict, metrics_b: dict) -> dict:
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
