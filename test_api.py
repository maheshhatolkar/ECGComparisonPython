import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_api():
    print("Testing /records (should be empty or list existing)...")
    resp = requests.get(f"{BASE_URL}/records")
    if resp.status_code != 200:
        print("Error /records:", resp.status_code, resp.text)
        return
    print("Records count:", len(resp.json()))
    
    print("\nTesting /save_record...")
    with open("test_ecg.png", "rb") as f:
        files = {"file": ("test_ecg.png", f, "image/png")}
        data = {
            "metadata": json.dumps({"patient_id": "API-Test-01", "ecg_datetime": "2026-06-21 12:00"}),
            "pixels_per_mm": 20.0,
            "prominence": 0.5
        }
        resp = requests.post(f"{BASE_URL}/save_record", data=data, files=files)
        
    if resp.status_code != 200:
        print("Error /save_record:", resp.status_code, resp.text)
        return
    res_json = resp.json()
    record_id = res_json.get("record_id")
    print(f"Saved record ID: {record_id}")
    
    print(f"\nTesting /record/{record_id}...")
    resp = requests.get(f"{BASE_URL}/record/{record_id}")
    if resp.status_code != 200:
        print("Error /record:", resp.status_code, resp.text)
        return
    rec = resp.json()
    print("Fetched record patient_name:", rec.get("patient_name") or rec.get("metadata", {}).get("patient_id"))
    analysis = rec.get("analysis", {})
    metrics = analysis.get("metrics", {})
    print("Metrics extracted:", metrics)
    
    print("\nTesting /compare...")
    data = {
        "record_a": record_id,
        "record_b": record_id
    }
    resp = requests.post(f"{BASE_URL}/compare", data=data)
    if resp.status_code != 200:
        print("Error /compare:", resp.status_code, resp.text)
        return
    compare_res = resp.json()
    print("Alignment method used:", compare_res.get("alignment_method"))
    print("Delta metrics:", compare_res.get("delta_metrics"))
    
    print("\nAll detailed API workflow tests completed successfully!")

if __name__ == "__main__":
    test_api()
