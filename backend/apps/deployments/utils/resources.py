"""
Resource estimation and system requirements checking utilities.
"""
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any

import psutil

logger = logging.getLogger(__name__)


_HEAVY_DEPS = {
    'torch': (1.0, 2048), 'pytorch': (1.0, 2048),
    'tensorflow': (1.0, 2048), 'transformers': (1.0, 1536),
    'playwright': (0.5, 1024), 'selenium': (0.5, 1024),
    'pandas': (0.5, 1024), 'numpy': (0.5, 768),
    'scipy': (0.5, 1024), 'scikit-learn': (0.5, 1024),
    'opencv-python': (0.5, 1024), 'pillow': (0.25, 768),
    'spacy': (0.5, 1024), 'celery': (0.25, 768),
}


def parse_ai_resource_recommendation(ai_response: str) -> dict:
    if not ai_response:
        return {}

    try:
        json_match = re.search(
            r'```(?:json)?\s*\n?(.*?)\n?\s*```',
            ai_response,
            re.DOTALL
        )
        if json_match:
            raw = json_match.group(1).strip()
        else:
            brace_match = re.search(r'\{.*\}', ai_response, re.DOTALL)
            if brace_match:
                raw = brace_match.group(0)
            else:
                return {}

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}

        result: dict[str, Any] = {}

        res = parsed.get('resources', {})
        if isinstance(res, dict):
            cpu = res.get('cpu_cores')
            mem = res.get('memory_mb')
            if cpu is not None or mem is not None:
                result['resources'] = {}
                if isinstance(cpu, (int, float)) and 0.1 <= cpu <= 16:
                    result['resources']['cpu_cores'] = round(float(cpu), 2)
                if isinstance(mem, (int, float)) and 128 <= mem <= 32768:
                    result['resources']['memory_mb'] = int(mem)

        env = parsed.get('required_env_vars', {})
        if isinstance(env, dict):
            result['required_env_vars'] = {
                str(k): str(v) for k, v in env.items()
                if isinstance(k, str) and k.strip()
            }

        issues = parsed.get('issues', [])
        if isinstance(issues, list):
            result['issues'] = [str(i) for i in issues[:10]]

        diag = parsed.get('diagnosis', '')
        if isinstance(diag, str):
            result['diagnosis'] = diag[:5000]

        return result

    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.debug("Failed to parse AI resource recommendation: %s", e)
        return {}


def estimate_resources_from_deps(source_dir: str) -> dict:
    max_cpu = 0.0
    max_mem = 0

    for req_file in ('requirements.txt', 'requirements/base.txt',
                     'requirements/production.txt'):
        path = os.path.join(source_dir, req_file)
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        pkg = (line.strip().split('==')[0].split('>=')[0]
                               .split('<=')[0].split('[')[0].split('#')[0]
                               .strip().lower())
                        if pkg in _HEAVY_DEPS:
                            cpu, mem = _HEAVY_DEPS[pkg]
                            max_cpu = max(max_cpu, cpu)
                            max_mem = max(max_mem, mem)
            except OSError:
                pass

    if max_cpu > 0 or max_mem > 0:
        return {'cpu_cores': max_cpu, 'memory_mb': max_mem}
    return {}


def check_requirements(min_ram_gb=None, min_cpu_cores=None, min_disk_gb=None, gpu_required=False):
    if min_ram_gb:
        total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        if total_ram_gb < min_ram_gb:
            return False, f"Insufficient RAM: {total_ram_gb:.1f}GB total, {min_ram_gb}GB required."

    if min_cpu_cores:
        cpu_count = psutil.cpu_count(logical=True)
        if cpu_count < min_cpu_cores:
            return False, f"Insufficient CPU cores: {cpu_count} cores, {min_cpu_cores} required."

    if min_disk_gb:
        _total, _used, free = shutil.disk_usage("/")
        free_gb = free / (1024 ** 3)
        if free_gb < min_disk_gb:
            return False, f"Insufficient Disk Space: {free_gb:.1f}GB free, {min_disk_gb}GB required for installation."

    if gpu_required:
        try:
            subprocess.run(["nvidia-smi"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False, "NVIDIA GPU required but nvidia-smi was not found or failed. Ensure NVIDIA drivers and container toolkit are installed."

    return True, ""
