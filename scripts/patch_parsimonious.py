import os
import sys

# Find the parsimonious package location
try:
    import parsimonious
    pkg_dir = os.path.dirname(parsimonious.__file__)
except ImportError:
    # Fallback: find it in site-packages
    import site
    for dir in site.getsitepackages():
        candidate = os.path.join(dir, "parsimonious")
        if os.path.exists(candidate):
            pkg_dir = candidate
            break
    else:
        print("❌ Could not find parsimonious package")
        sys.exit(1)

target = os.path.join(pkg_dir, "expressions.py")
if not os.path.exists(target):
    print(f"❌ {target} not found")
    sys.exit(1)

with open(target, "r") as f:
    content = f.read()

if "getfullargspec" in content:
    print("✅ Already patched")
    sys.exit(0)

# Replace the deprecated import
content = content.replace(
    "from inspect import getargspec",
    "from inspect import getfullargspec as getargspec",
)

with open(target, "w") as f:
    f.write(content)

print(f"✅ Patched {target}")
