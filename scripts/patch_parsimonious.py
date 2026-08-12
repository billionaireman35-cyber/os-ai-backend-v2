import os
import parsimonious

target = os.path.join(os.path.dirname(parsimonious.__file__), "expressions.py")
with open(target, "r") as f:
    content = f.read()

content = content.replace(
    "from inspect import getargspec",
    "from inspect import getfullargspec as getargspec",
)

with open(target, "w") as f:
    f.write(content)

print(f"✅ Patched {target}")
