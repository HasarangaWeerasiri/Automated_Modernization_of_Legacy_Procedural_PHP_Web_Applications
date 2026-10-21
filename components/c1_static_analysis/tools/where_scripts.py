"""Print the folder that holds c1-migrate(.exe) after `pip install -e .`; empty if not found. Used by install.bat."""
import os
import sysconfig

exe = "c1-migrate.exe" if os.name == "nt" else "c1-migrate"
cands = [sysconfig.get_path("scripts"), sysconfig.get_path("scripts", os.name + "_user")]
print(next((d for d in cands if d and os.path.exists(os.path.join(d, exe))), ""))
