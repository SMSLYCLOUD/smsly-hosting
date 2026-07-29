import logging
import os
import re

logger = logging.getLogger(__name__)


class ParsersMixin:
    def _scan_env_example(self) -> None:
        candidates = [
            os.path.join(self.source_dir, ".env.example"),
            os.path.join(self.source_dir, ".env.production"),
            os.path.join(self.source_dir, ".env"),
        ]
        for sub in ("backend", "app", "server", "src", "api"):
            candidates.append(os.path.join(self.source_dir, sub, ".env.example"))
            candidates.append(os.path.join(self.source_dir, sub, ".env.production"))
            candidates.append(os.path.join(self.source_dir, sub, ".env"))

        seen: set[str] = set()
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line or line.startswith("#"):
                            continue
                        line = re.sub(r"^export\s+", "", line)
                        if "=" not in line:
                            continue
                        key, _, val = line.partition("=")
                        key = key.strip().upper()
                        val = val.strip().strip("\"'")
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        if val and val.lower() not in (
                            "changeme", "change_me", "your-value-here",
                            "your_secret_key", "",
                        ):
                            self.env_example_vars[key] = val
                        else:
                            self.env_example_vars[key] = ""
            except OSError:
                continue

    def _scan_secrets_manifest(self) -> None:
        import yaml

        candidates = [
            os.path.join(self.source_dir, "SECRETS-MANIFEST.yaml"),
            os.path.join(self.source_dir, "SECRETS-MANIFEST.yml"),
        ]
        for sub in ("backend", "app", "server"):
            candidates.append(os.path.join(self.source_dir, sub, "SECRETS-MANIFEST.yaml"))
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    self.secrets_manifest = data
                return
            except Exception as e:
                logger.warning("Failed to parse SECRETS-MANIFEST at %s: %s", path, e)
