import os

root_dir = r"D:\NODIA\ODOO + n8n+Watssap"
search_term = "komjxjpvsoxlrzdhekqn"

found = False
for dirpath, dirnames, filenames in os.walk(root_dir):
    if "node_modules" in dirpath or ".next" in dirpath or ".git" in dirpath:
        continue
    for fname in filenames:
        fpath = os.path.join(dirpath, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                if search_term in f.read():
                    print(f"Found in: {os.path.relpath(fpath, root_dir)}")
                    found = True
        except:
            pass

if not found:
    print(f"Term '{search_term}' not found in the codebase.")
