import sys
sys.path.append('c:\\Projects\\ECGComparisonPython')
from ECGComparisonPython import align_and_compare_api, load_record

analysis_a = load_record(38).get("analysis")
analysis_b = load_record(39).get("analysis")

print("r_peaks in test_api2.py:", analysis_a['features']['r_peaks'])
print("type:", type(analysis_a['features']['r_peaks'][0]))

import json
import requests
import os
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")

def my_align(analysis_a, analysis_b):
    data = {"analysis_a": json.dumps(analysis_a), "analysis_b": json.dumps(analysis_b)}
    res = requests.post(f"{BACKEND_URL}/compare", data=data)
    print("STATUS", res.status_code)
    print("TEXT", res.text[:200])
    return res.json() if res.status_code == 200 else {}

comp_res = my_align(analysis_a, analysis_b)
