import hashlib
import logging
import os
import re

import requests

from apps.deployments.utils import build_local_source_bundle as utils_build_bundle
from apps.deployments.utils import get_source_root_dir as utils_get_source_root

logger = logging.getLogger(__name__)


def _source_root_dir() -> str:
    return utils_get_source_root()


def _build_local_source_bundle() -> str:
    return utils_build_bundle()


def _load_install_script():
    required_sha = os.environ.get("SMSLY_INSTALL_SCRIPT_SHA256", "").strip()

    candidates = [
        "/app/install.sh",
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../../../../../install.sh")
        ),
        os.path.abspath(os.path.join(os.getcwd(), "install.sh")),
    ]

    if not required_sha:
        for path in candidates:
            if os.path.isfile(path):
                try:
                    with open(path, "rb") as f:
                        file_content = f.read()
                        if file_content.strip():
                            required_sha = hashlib.sha256(file_content).hexdigest()
                            logger.info("Auto-calculated SMSLY_INSTALL_SCRIPT_SHA256 from %s: %s", path, required_sha)
                            break
                except Exception as e:
                    logger.warning("Failed to auto-calculate SHA from %s: %s", path, e)

    def _verify(content: str, source: str):
        if not required_sha:
            if source.startswith("url:"):
                raise ValueError(
                    "SMSLY_INSTALL_SCRIPT_SHA256 is not set and no local install.sh found. "
                    "Refusing to execute an unverified script from the network. "
                    "Set SMSLY_INSTALL_SCRIPT_SHA256 to the SHA-256 of your install.sh."
                )
            logger.warning(
                "SMSLY_INSTALL_SCRIPT_SHA256 is missing and no local install.sh found. "
                "Skipping checksum verification for %s.", source
            )
            return
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest.lower() != required_sha.lower():
            raise ValueError(
                f"install.sh checksum mismatch from {source}: expected {required_sha}, got {digest}"
            )

    for path in candidates:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as install_file:
                content = install_file.read()
                _verify(content, f"local:{path}")
                lib_dir = os.path.join(os.path.dirname(path), "lib")
                if os.path.isdir(lib_dir):
                    inline_lines = []
                    for lib_file in sorted(os.listdir(lib_dir)):
                        if not lib_file.endswith(".sh"):
                            continue
                        if lib_file in ("fresh.sh", "update.sh"):
                            continue
                        lib_path = os.path.join(lib_dir, lib_file)
                        with open(lib_path, encoding="utf-8") as lf:
                            lib_content = lf.read()
                        inline_lines.append(
                            f"# --- lib/{lib_file} ---\n{lib_content}\n"
                            f"# --- end lib/{lib_file} ---"
                        )
                    if inline_lines:
                        inline_block = "\n\n".join(inline_lines)
                        _start = "--- BEGIN_LIB_SOURCING ---"
                        _end = "--- END_LIB_SOURCING ---"
                        _s = content.find(_start)
                        _e = content.find(_end)
                        if _s != -1 and _e != -1 and _e > _s:
                            content = (
                                content[:_s]
                                + "\n" + inline_block + "\n"
                                + content[_e + len(_end) + 1:]
                            )
                        else:
                            content = re.sub(
                                r'for lib in "\$LIB_DIR"/\*\.sh; do\s*\n'
                                r'(?:\s*#.*\n)*'
                                r'\s*case "\$lib" in \*/fresh\.sh\|\*/update\.sh\) continue ;; esac\s*\n'
                                r'\s*\[ -f "\$lib" \] && source "\$lib"\s*\n'
                                r'\s*done\s*\n',
                                "\n" + inline_block + "\n",
                                content,
                            )
            return content, f"local:{path}"

    script_url = (
        os.environ.get(
            "SMSLY_INSTALL_SCRIPT_URL",
            "https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh",
        )
        .strip()
    )
    response = requests.get(script_url, timeout=30)
    response.raise_for_status()
    content = response.text
    _verify(content, f"url:{script_url}")
    if not content.strip():
        raise ValueError("Downloaded installer script is empty")
    return content, f"url:{script_url}"
