import os
import re

# Buscar IDs de Meta en el código (cadenas de 15 o 16 dígitos)
pattern = re.compile(r'\b\d{15,16}\b')

root_dir = r"D:\NODIA\ODOO + n8n+Watssap"
extensions = (".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".env", ".local")

print("Searching for Meta App IDs (15-16 digits) in codebase...")
found = []
for dirpath, dirnames, filenames in os.walk(root_dir):
    if "node_modules" in dirpath or ".next" in dirpath or ".git" in dirpath:
        continue
    for fname in filenames:
        if fname.endswith(extensions):
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                matches = pattern.findall(content)
                if matches:
                    print(f"File: {os.path.relpath(fpath, root_dir)}")
                    for m in set(matches):
                        print(f"  - Match: {m}")
                        found.append((fpath, m))
            except Exception as e:
                pass

if not found:
    print("No 15-16 digit IDs found.")
