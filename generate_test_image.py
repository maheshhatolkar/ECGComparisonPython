from PIL import Image
import numpy as np

def _synthetic_ecg_image(width=800, height=300):
    img = np.full((height, width, 3), 245, dtype=np.uint8)
    mid = height // 2
    for x in range(width):
        if x % 200 == 50:
            y = mid - 40
        elif x % 200 == 52:
            y = mid + 20
        else:
            y = int(mid + 5 * np.sin(x / 15))
        img[max(0, y - 1):min(height, y + 2), x] = [0, 0, 0]
    return Image.fromarray(img)

img = _synthetic_ecg_image()
img.save("test_ecg.png")
print("Saved test_ecg.png")
