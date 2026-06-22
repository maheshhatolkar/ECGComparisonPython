import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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



