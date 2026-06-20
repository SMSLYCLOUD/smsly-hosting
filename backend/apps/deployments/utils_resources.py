"""Utility for checking system resources."""
import logging
import shutil

import psutil

logger = logging.getLogger(__name__)

def check_requirements(min_ram_gb=None, min_cpu_cores=None, min_disk_gb=None, gpu_required=False):
    """
    Check if the host system meets the specified requirements.
    Returns: (bool, str) - (success, error_message)
    """
    # 1. Check RAM
    if min_ram_gb:
        total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        if total_ram_gb < min_ram_gb:
            return False, f"Insufficient RAM: {total_ram_gb:.1f}GB total, {min_ram_gb}GB required."

    # 2. Check CPU Cores
    if min_cpu_cores:
        cpu_count = psutil.cpu_count(logical=True)
        if cpu_count < min_cpu_cores:
            return False, f"Insufficient CPU cores: {cpu_count} cores, {min_cpu_cores} required."

    # 3. Check Disk Space
    if min_disk_gb:
        # Check current partition where services are likely deployed
        _total, _used, free = shutil.disk_usage("/")
        free_gb = free / (1024 ** 3)
        if free_gb < min_disk_gb:
            return False, f"Insufficient Disk Space: {free_gb:.1f}GB free, {min_disk_gb}GB required for installation."

    # 4. Check GPU (Best effort)
    if gpu_required:
        # Check for NVIDIA GPU via nvidia-smi
        import subprocess
        try:
            subprocess.run(["nvidia-smi"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False, "NVIDIA GPU required but nvidia-smi was not found or failed. Ensure NVIDIA drivers and container toolkit are installed."

    return True, ""
