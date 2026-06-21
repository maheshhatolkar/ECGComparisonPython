import os
import glob
import re

js_files = glob.glob('mobile_app/screens/*.js')
count = 0

for jf in js_files:
    with open(jf, 'r', encoding='utf-8') as f:
        content = f.read()

    # Use regex to find any quote type surrounding the hardcoded IP
    # and replace the whole string with a template literal using API_URL.
    new_content = re.sub(r'[\'"`]http://10\.0\.2\.2:8000([^\'"`]*)[\'"`]', r'`${API_URL}\1`', content)
    
    if new_content != content:
        # Prepend the import if it's not already there
        if 'import { API_URL }' not in new_content:
            new_content = 'import { API_URL } from "../config";\n' + new_content
            
        with open(jf, 'w', encoding='utf-8') as f:
            f.write(new_content)
        count += 1
        print(f"Updated {jf}")

print(f"Done. Updated {count} files.")
