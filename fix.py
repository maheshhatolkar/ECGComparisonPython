import glob
import os

for f in glob.glob('*.py'):
    if f == 'fix.py': continue
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    if '\\n\\n' in content:
        content = content.replace('\\n\\n', '\n\n')
        with open(f, 'w', encoding='utf-8') as file:
            file.write(content)
        print(f"Fixed {f}")
