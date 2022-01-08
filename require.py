# Allows you to "require"/install any pip module at runtime.
# Fortunately, Blender comes with Python and pip, so this can be called by addons.


import importlib
import subprocess
import sys

def find_python():
    return sys.executable

python = find_python()
has_pip = False

def ensure_pip():
    global has_pip
    if not has_pip:
        print("Looking for pip")
        subprocess.call([python, "-m", "ensurepip"], shell=True)
        has_pip = True

def install(module):
    print(f"Installing {module}")
    ensure_pip()
    subprocess.call([python, "-m", "pip", "install", module], shell=True, stdout=sys.stdout, stderr=sys.stderr)

def require(modules):
    for module in modules:
        try:
            importlib.import_module(module)
            # print(f"Found module {module}")
        except:
            print(f"{module} not installed!")
            install(module)
