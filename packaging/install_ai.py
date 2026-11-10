"""Install or repair the AI components (PyTorch + TerraTorch) into the app's own Python.

Detects an NVIDIA GPU with nvidia-smi:
  * GPU found  -> PyTorch built for CUDA 12.8 (required by RTX 50-series cards such as the RTX 5060)
  * no GPU     -> CPU PyTorch
Run from the Start menu ("Install or repair AI components") or by the installer.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
FROM_INSTALLER = "--from-installer" in sys.argv


def uv() -> list[str]:
    exe = ROOT / "tools" / "uv.exe"
    if exe.exists():
        return [str(exe)]
    found = shutil.which("uv")
    if found:
        return [found]
    return [PY, "-m", "uv"]


def gpu_name() -> str | None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run([smi, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def run(cmd: list[str]) -> int:
    print("\n> " + " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def main() -> int:
    print("VU3INI Climate Studio - AI components")
    print("=====================================")
    g = gpu_name()
    index = "https://download.pytorch.org/whl/cu128" if g else "https://download.pytorch.org/whl/cpu"
    print(f"NVIDIA GPU: {g}" if g else "No NVIDIA GPU found: installing the CPU version (AI maps still work, more slowly).")
    print("Downloading about 3 GB with a GPU, less without. Please keep this window open.\n")
    base = uv() + ["pip", "install", "--python", PY, "--break-system-packages"]
    code = run(base + ["torch", "torchvision", "--index-url", index])
    if code == 0:
        code = run(base + ["terratorch", "huggingface_hub", "pyyaml",
                           "--extra-index-url", index, "--index-strategy", "unsafe-best-match"])
    print()
    check = subprocess.run([PY, "-c", "import torch;print('PyTorch', torch.__version__, '| GPU usable:', torch.cuda.is_available(),"
                                      " '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"],
                           capture_output=True, text=True)
    print(check.stdout or check.stderr)
    if code == 0 and check.returncode == 0:
        print("AI components are installed.")
    else:
        print("The AI components could not be fully installed. The rest of the app works normally.")
        print("Check the internet connection and run 'Install or repair AI components' from the Start menu again.")
    if not FROM_INSTALLER:
        input("\nPress Enter to close this window.")
    return 0  # never fail the main installation because of the optional AI step


if __name__ == "__main__":
    sys.exit(main())
