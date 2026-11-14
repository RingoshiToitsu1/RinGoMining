"""One-time installer. Run inside the virtual environment (setup.bat / setup.sh do this for you).

1. Installs PyTorch: the NVIDIA (CUDA) build if an NVIDIA card is found, otherwise CPU.
2. Installs the other Python packages.
3. Downloads TripoSR's code (pinned version) into vendor/TripoSR.
The model weights (~1.7 GB) download automatically the first time you run the app.
"""

import io
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRIPOSR_COMMIT = "107cefdc244c39106fa830359024f6a2f1c78871"
TRIPOSR_ZIP = f"https://github.com/VAST-AI-Research/TripoSR/archive/{TRIPOSR_COMMIT}.zip"
# Newest first; the first one that has a wheel for this Python wins.
CUDA_INDEXES = ["cu130", "cu128", "cu126"]


def pip(*args: str) -> bool:
    return subprocess.call([sys.executable, "-m", "pip", "install", *args]) == 0


def has_nvidia_gpu() -> bool:
    if shutil.which("nvidia-smi") is None:
        return False
    return subprocess.call(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def install_torch():
    system = platform.system()
    if system in ("Windows", "Linux") and has_nvidia_gpu():
        print("\n== NVIDIA GPU found: installing PyTorch with CUDA ==")
        for tag in CUDA_INDEXES:
            if pip("torch", "--index-url", f"https://download.pytorch.org/whl/{tag}"):
                return
            print(f"   ({tag} build not available, trying the next one)")
        print("!! Couldn't install a CUDA build; falling back to CPU.")
    else:
        print("\n== No NVIDIA GPU found: installing CPU PyTorch (works, just slower) ==")
    if system == "Linux":
        ok = pip("torch", "--index-url", "https://download.pytorch.org/whl/cpu")
    else:  # Windows and macOS: the default PyPI build is the CPU / Apple build
        ok = pip("torch")
    if not ok:
        sys.exit("PyTorch install failed. See the messages above.")


def install_triposr():
    target = HERE / "vendor" / "TripoSR"
    if (target / "tsr").is_dir():
        print("\n== TripoSR code already present ==")
        return
    print("\n== Downloading TripoSR code ==")
    if shutil.which("git"):
        repo = "https://github.com/VAST-AI-Research/TripoSR.git"
        if subprocess.call(["git", "clone", "-q", repo, str(target)]) == 0 and subprocess.call(
            ["git", "-C", str(target), "checkout", "-q", TRIPOSR_COMMIT]
        ) == 0:
            return
        shutil.rmtree(target, ignore_errors=True)
        print("   (git download failed, trying zip)")
    with urllib.request.urlopen(TRIPOSR_ZIP) as r:
        data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = z.namelist()[0].split("/")[0]
        z.extractall(HERE / "vendor")
    (HERE / "vendor" / root).rename(target)


def main():
    if sys.version_info < (3, 10) or sys.version_info >= (3, 13):
        print(f"!! You're on Python {platform.python_version()}. This was tested on 3.11; 3.10-3.12 should work.")
    pip("--upgrade", "pip")
    install_torch()
    print("\n== Installing the other packages ==")
    if not pip("-r", str(HERE / "requirements.txt")):
        sys.exit("Package install failed. See the messages above.")
    install_triposr()

    import torch

    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    print("\n==============================================")
    print(" Setup finished.")
    print(f" Running on: {gpu or 'CPU (no NVIDIA GPU in use)'}")
    print(" Start the app with start.bat (Windows) or ./start.sh (Mac/Linux).")
    print("==============================================")


if __name__ == "__main__":
    main()
