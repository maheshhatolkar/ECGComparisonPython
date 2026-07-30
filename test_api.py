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
    
    print("\nTesting /analysis/plot...")
    resp = requests.post(f"{BASE_URL}/analysis/plot", json=analysis)
    if resp.status_code != 200:
        print("Error /analysis/plot:", resp.status_code, resp.text)
    else:
        plot_res = resp.json()
        print("Analysis plot base64 length:", len(plot_res.get("plot_base64", "")))

    print("\nTesting /compare/plot...")
    resp = requests.post(f"{BASE_URL}/compare/plot", json={
        "aligned_a": compare_res.get("aligned_a", []),
        "aligned_b": compare_res.get("aligned_b", [])
    })
    if resp.status_code != 200:
        print("Error /compare/plot:", resp.status_code, resp.text)
    else:
        plot_res = resp.json()
        print("Compare plot base64 length:", len(plot_res.get("plot_base64", "")))

    print("\nTesting /tables...")
    resp = requests.get(f"{BASE_URL}/tables")
    if resp.status_code != 200:
        print("Error /tables:", resp.status_code, resp.text)
        return
    tables = resp.json()
    print("Tables found:", tables)

    if tables:
        t = tables[0]
        print(f"\nTesting /table/{t}...")
        resp = requests.get(f"{BASE_URL}/table/{t}")
        if resp.status_code != 200:
            print(f"Error /table/{t}:", resp.status_code, resp.text)
        else:
            table_data = resp.json()
            print(f"Rows in {t}:", len(table_data))
    
    print("\nTesting new settings endpoints...")
    resp = requests.post(f"{BASE_URL}/settings/test_setting", data={"value": "test_val"})
    if resp.status_code != 200:
        print("Error POST /settings/test_setting:", resp.status_code, resp.text)
    resp = requests.get(f"{BASE_URL}/settings/test_setting")
    if resp.status_code != 200 or resp.json().get("value") != "test_val":
        print("Error GET /settings/test_setting:", resp.status_code, resp.text)
    else:
        print("Settings endpoints tested successfully!")

    print("\nTesting users and audit log endpoints...")
    resp = requests.get(f"{BASE_URL}/users")
    if resp.status_code != 200:
        print("Error GET /users:", resp.status_code, resp.text)
    else:
        print("Users list length:", len(resp.json()))

    test_username = f"api_test_user_{int(time.time())}"
    test_user_data = {
        "username": test_username,
        "display_name": "API Test User",
        "role": "Researcher",
        "password": "testpassword123",
        "enabled": "true"
    }
    resp = requests.post(f"{BASE_URL}/users", data=test_user_data)
    if resp.status_code != 200:
        print("Error POST /users:", resp.status_code, resp.text)
    else:
        print(f"Created test user {test_username} successfully!")

    resp = requests.get(f"{BASE_URL}/audit_logs")
    if resp.status_code != 200:
        print("Error GET /audit_logs:", resp.status_code, resp.text)
    else:
        print("Audit logs count:", len(resp.json()))

    resp = requests.post(f"{BASE_URL}/audit_log", data={
        "event_type": "api_test_event",
        "outcome": "success",
        "details": "Integration test check"
    })
    if resp.status_code != 200:
        print("Error POST /audit_log:", resp.status_code, resp.text)
    else:
        print("Logged audit event successfully!")

    print("\nTesting /detect_grid_spacing...")
    with open("test_ecg.png", "rb") as f:
        resp = requests.post(f"{BASE_URL}/detect_grid_spacing", files={"file": ("test_ecg.png", f, "image/png")})
    if resp.status_code != 200:
        print("Error /detect_grid_spacing:", resp.status_code, resp.text)
    else:
        print("Detected grid spacing:", resp.json().get("grid_spacing"))

    print("\nTesting /compare/delta_plot...")
    resp = requests.post(f"{BASE_URL}/compare/delta_plot", json={"delta": [0.0, 0.1, -0.1, 0.2]})
    if resp.status_code != 200:
        print("Error /compare/delta_plot:", resp.status_code, resp.text)
    else:
        print("Delta plot base64 length:", len(resp.json().get("plot_base64", "")))

    print(f"\nTesting DELETE /record/{record_id}...")
    resp = requests.delete(f"{BASE_URL}/record/{record_id}")
    if resp.status_code != 200:
        print("Error DELETE /record:", resp.status_code, resp.text)
    else:
        print("Deleted record successfully!")

    print("\nAll detailed API workflow tests completed successfully!")

if __name__ == "__main__":
    test_api()
