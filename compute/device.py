"""Automatic CPU/GPU selection for local work.

Earth Engine computes in Google's cloud, so this module only decides where
*local* tasks run: Prithvi inference, XGBoost training, heavy NumPy work and
local LLMs. Every local task asks `choose(task, size)` for a device and runs
through `run_with_fallback`, which shrinks the batch on CUDA out-of-memory and
finally falls back to CPU instead of crashing.

The app must work fully on a PC without an NVIDIA GPU or without PyTorch.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import psutil

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

@dataclass
class GPUInfo:
    name: str
    vram_total_gb: float
    vram_free_gb: float
    compute_capability: str = ""
    driver: str = ""


@dataclass
class Hardware:
    cpu_cores: int
    cpu_threads: int
    ram_gb: float
    torch_version: str = ""
    torch_cuda: str = ""  # CUDA version PyTorch was built for
    cuda_usable: bool = False
    gpus: list[GPUInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def gpu(self) -> GPUInfo | None:
        return self.gpus[0] if self.gpus else None

    def describe(self) -> str:
        g = self.gpu
        cpu = f"{self.cpu_cores} cores / {self.cpu_threads} threads, {self.ram_gb:.0f} GB RAM"
        if g and self.cuda_usable:
            return f"GPU: {g.name} ({g.vram_total_gb:.1f} GB). CPU: {cpu}"
        return f"CPU only: {cpu}"


def _nvidia_smi() -> list[GPUInfo]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except Exception:
        # Older drivers do not support compute_cap; retry without it.
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout
        except Exception:
            return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            gpus.append(GPUInfo(
                name=parts[0], vram_total_gb=float(parts[1]) / 1024, vram_free_gb=float(parts[2]) / 1024,
                driver=parts[3] if len(parts) > 3 else "", compute_capability=parts[4] if len(parts) > 4 else "",
            ))
        except (ValueError, IndexError):
            continue
    return gpus


def detect() -> Hardware:
    hw = Hardware(
        cpu_cores=psutil.cpu_count(logical=False) or 1,
        cpu_threads=psutil.cpu_count(logical=True) or 1,
        ram_gb=psutil.virtual_memory().total / 1024**3,
    )
    smi = _nvidia_smi()
    try:
        import torch  # noqa: F401

        hw.torch_version = torch.__version__
        hw.torch_cuda = torch.version.cuda or ""
        if torch.cuda.is_available():
            hw.cuda_usable = True
            gpus = []
            for i in range(torch.cuda.device_count()):
                p = torch.cuda.get_device_properties(i)
                free, total = torch.cuda.mem_get_info(i)
                gpus.append(GPUInfo(
                    name=p.name, vram_total_gb=total / 1024**3, vram_free_gb=free / 1024**3,
                    compute_capability=f"{p.major}.{p.minor}",
                    driver=smi[i].driver if i < len(smi) else "",
                ))
            hw.gpus = gpus
            # Blackwell (RTX 50-series, compute capability 12.x) needs a CUDA 12.8+ build.
            g = hw.gpu
            if g and g.compute_capability.startswith("12") and hw.torch_cuda and _ver(hw.torch_cuda) < (12, 8):
                hw.cuda_usable = False
                hw.notes.append(
                    f"{g.name} needs a PyTorch build for CUDA 12.8 or newer; this one is for CUDA "
                    f"{hw.torch_cuda}. Re-run setup to install the right build. Using CPU for now."
                )
        else:
            hw.gpus = smi
            if smi:
                hw.notes.append(
                    "An NVIDIA GPU is present but PyTorch cannot use it (CPU-only PyTorch build or driver "
                    "problem). Re-run setup to install the GPU build. Using CPU for now."
                )
    except ImportError:
        hw.gpus = smi
        hw.notes.append("PyTorch is not installed; AI features that need it are unavailable. Other features work.")
    return hw


def _ver(s: str) -> tuple[int, int]:
    try:
        a, b, *_ = (s.split(".") + ["0"])
        return int(a), int(b)
    except ValueError:
        return (0, 0)


# ---------------------------------------------------------------------------
# Task profiles and device choice
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskProfile:
    """What a task needs before GPU is worth it."""
    min_vram_gb: float          # free VRAM needed at the default batch size
    gpu_min_size: int           # below this many work units, CPU is faster (GPU start-up costs more)
    uses_torch: bool = True


TASKS: dict[str, TaskProfile] = {
    "prithvi": TaskProfile(min_vram_gb=3.0, gpu_min_size=1),         # tiles; GPU worth it from the first tile
    "xgboost": TaskProfile(min_vram_gb=1.0, gpu_min_size=100_000, uses_torch=False),  # training rows
    "raster": TaskProfile(min_vram_gb=1.0, gpu_min_size=50_000_000, uses_torch=False),  # pixels (CuPy, optional)
    "llm": TaskProfile(min_vram_gb=4.0, gpu_min_size=1, uses_torch=False),
}


@dataclass
class Device:
    kind: str            # "cuda" or "cpu"
    reason: str
    batch_size: int = 1

    @property
    def torch_device(self) -> str:
        return "cuda:0" if self.kind == "cuda" else "cpu"

    def label(self) -> str:
        return "GPU" if self.kind == "cuda" else "CPU"


class DeviceManager:
    """Chooses CPU or GPU per task. One instance per app (see `manager()`)."""

    def __init__(self, mode: str = "auto", hardware: Hardware | None = None, benchmarks: dict[str, Any] | None = None):
        self.mode = mode  # auto | cpu | gpu
        self.hw = hardware or detect()
        self.benchmarks: dict[str, Any] = benchmarks or {}
        self.last: dict[str, Device] = {}
        self._configure_cpu_threads()

    def _configure_cpu_threads(self) -> None:
        n = max(1, self.hw.cpu_threads - 1)  # leave one thread for the UI
        os.environ.setdefault("OMP_NUM_THREADS", str(n))
        try:
            import torch

            torch.set_num_threads(n)
        except Exception:
            pass

    def gpu_ok(self, task: str) -> bool:
        prof = TASKS.get(task, TaskProfile(1.0, 1))
        g = self.hw.gpu
        if not g:
            return False
        if prof.uses_torch and not self.hw.cuda_usable:
            return False
        if task == "xgboost" and not _xgboost_has_cuda():
            return False
        if task == "raster" and not _has_cupy():
            return False
        free = self._free_vram_gb()
        return free >= prof.min_vram_gb

    def _free_vram_gb(self) -> float:
        try:
            import torch

            if self.hw.cuda_usable:
                free, _ = torch.cuda.mem_get_info(0)
                return free / 1024**3
        except Exception:
            pass
        g = self.hw.gpu
        return g.vram_free_gb if g else 0.0

    def choose(self, task: str, size: int = 1) -> Device:
        prof = TASKS.get(task, TaskProfile(1.0, 1))
        if self.mode == "cpu":
            d = Device("cpu", "Forced to CPU in Settings")
        elif self.mode == "gpu":
            if self.gpu_ok(task):
                d = Device("cuda", "Forced to GPU in Settings")
            else:
                d = Device("cpu", "GPU forced in Settings but not usable for this task; using CPU")
        elif not self.gpu_ok(task):
            d = Device("cpu", "No usable GPU for this task" if not self.hw.gpu else "Not enough free GPU memory or GPU build missing")
        elif size < prof.gpu_min_size:
            d = Device("cpu", f"Small job ({size:,} units): CPU is faster than starting the GPU")
        else:
            bench = self.benchmarks.get(task)
            if bench and bench.get("cpu_s") and bench.get("gpu_s") and bench["gpu_s"] >= bench["cpu_s"]:
                d = Device("cpu", "Benchmark on this PC: CPU was faster for this task")
            else:
                d = Device("cuda", "GPU available with enough free memory")
        d.batch_size = self.default_batch(task, d)
        self.last[task] = d
        log.info("Device for %s (size %s): %s - %s", task, size, d.kind, d.reason)
        return d

    def default_batch(self, task: str, d: Device) -> int:
        if task != "prithvi":
            return 1
        if d.kind == "cpu":
            return 1
        free = self._free_vram_gb()
        return 8 if free >= 12 else 4 if free >= 6 else 2

    # -- running with fallback ------------------------------------------------

    def run_with_fallback(self, task: str, fn: Callable[[Device], Any], size: int = 1) -> tuple[Any, Device]:
        """Run fn(device). On CUDA out-of-memory: halve batch twice, then use CPU."""
        d = self.choose(task, size)
        attempts = 0
        while True:
            try:
                return fn(d), d
            except Exception as e:  # noqa: BLE001
                if d.kind == "cuda" and _is_oom(e):
                    _empty_cuda_cache()
                    attempts += 1
                    if attempts <= 2 and d.batch_size > 1:
                        d = Device("cuda", f"Retry after GPU memory error with batch {max(1, d.batch_size // 2)}", max(1, d.batch_size // 2))
                        log.warning("CUDA OOM in %s; retrying with batch %s", task, d.batch_size)
                        continue
                    log.warning("CUDA OOM in %s after retries; falling back to CPU", task)
                    d = Device("cpu", "GPU ran out of memory; fell back to CPU", 1)
                    self.last[task] = d
                    continue
                raise

    # -- benchmark ---------------------------------------------------------------

    def benchmark(self, seconds_budget: float = 20.0) -> dict[str, Any]:
        """Short timing run on each available device; result cached in settings."""
        results: dict[str, Any] = {}
        t_end = time.perf_counter() + seconds_budget
        try:
            import torch

            def conv_bench(dev: str) -> float:
                x = torch.randn(2, 6, 224, 224, device=dev)
                net = torch.nn.Sequential(
                    torch.nn.Conv2d(6, 64, 3, padding=1), torch.nn.ReLU(),
                    torch.nn.Conv2d(64, 64, 3, padding=1), torch.nn.ReLU(),
                    torch.nn.Conv2d(64, 2, 1),
                ).to(dev)
                with torch.no_grad():
                    net(x)  # warm-up
                    if dev.startswith("cuda"):
                        torch.cuda.synchronize()
                    t = time.perf_counter()
                    for _ in range(3):
                        net(x)
                    if dev.startswith("cuda"):
                        torch.cuda.synchronize()
                return (time.perf_counter() - t) / 3

            r: dict[str, float] = {"cpu_s": conv_bench("cpu")}
            if self.hw.cuda_usable and time.perf_counter() < t_end:
                r["gpu_s"] = conv_bench("cuda:0")
            results["prithvi"] = r
        except Exception as e:  # noqa: BLE001
            results["prithvi"] = {"error": str(e)}
        if _xgboost_has_cuda() and time.perf_counter() < t_end:
            results["xgboost"] = _xgb_bench()
        results["when"] = time.strftime("%Y-%m-%d %H:%M")
        results["hardware"] = self.hw.describe()
        self.benchmarks = results
        return results

    def status_text(self) -> str:
        mode = {"auto": "Auto", "cpu": "CPU (forced)", "gpu": "GPU (forced)"}.get(self.mode, self.mode)
        return f"Compute: {mode}. {self.hw.describe()}"


def _is_oom(e: Exception) -> bool:
    msg = str(e).lower()
    return "out of memory" in msg or e.__class__.__name__ in {"OutOfMemoryError", "CUDAOutOfMemoryError"}


def _empty_cuda_cache() -> None:
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:
        pass


def _xgboost_has_cuda() -> bool:
    try:
        import xgboost  # noqa: F401

        info = xgboost.build_info()
        return bool(info.get("USE_CUDA"))
    except Exception:
        return False


def _has_cupy() -> bool:
    try:
        import cupy  # noqa: F401

        return True
    except Exception:
        return False


def _xgb_bench() -> dict[str, float]:
    import numpy as np
    import xgboost as xgb

    rng = np.random.default_rng(0)
    X = rng.normal(size=(200_000, 8))
    y = X @ rng.normal(size=8) + rng.normal(size=200_000)
    out = {}
    for dev, key in (("cpu", "cpu_s"), ("cuda", "gpu_s")):
        t = time.perf_counter()
        xgb.XGBRegressor(n_estimators=50, device=dev, tree_method="hist").fit(X, y)
        out[key] = time.perf_counter() - t
    return out


# ---------------------------------------------------------------------------
# Singleton used by the app
# ---------------------------------------------------------------------------

_manager: DeviceManager | None = None


def manager() -> DeviceManager:
    global _manager
    if _manager is None:
        from core import settings as settings_mod

        s = settings_mod.load()
        _manager = DeviceManager(mode=s.device_mode, benchmarks=s.benchmark_cache)
        s.hardware_cache = asdict(_manager.hw)
        settings_mod.save(s)
    return _manager


def reset(mode: str | None = None) -> DeviceManager:
    """Re-detect hardware (after a driver change) or apply a new mode."""
    global _manager
    _manager = None
    m = manager()
    if mode:
        m.mode = mode
    return m
