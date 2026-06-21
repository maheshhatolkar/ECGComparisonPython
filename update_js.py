import os
import glob

with open('mobile_app/config.js', 'w', encoding='utf-8') as f:
    f.write('export const API_URL = "http://10.0.2.2:8000";\n')

js_files = glob.glob('mobile_app/screens/*.js')
for jf in js_files:
    with open(jf, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if 'http://10.0.2.2:8000' in content:
        content = 'import { API_URL } from "../config";\n' + content
        # simple replace of the string literal
        content = content.replace('"http://10.0.2.2:8000/login"', '`${API_URL}/login`')
        content = content.replace('"http://10.0.2.2:8000/records"', '`${API_URL}/records`')
        content = content.replace('"http://10.0.2.2:8000/analyze"', '`${API_URL}/analyze`')
        content = content.replace('"http://10.0.2.2:8000/save_record"', '`${API_URL}/save_record`')
        content = content.replace('"http://10.0.2.2:8000/compare"', '`${API_URL}/compare`')
        content = content.replace('"http://10.0.2.2:8000/tables"', '`${API_URL}/tables`')
        
        # also handle table requests
        if '"http://10.0.2.2:8000/table/"' in content:
            content = content.replace('"http://10.0.2.2:8000/table/"', '`${API_URL}/table/`')

        with open(jf, 'w', encoding='utf-8') as f:
            f.write(content)

print('Updated mobile endpoints')
