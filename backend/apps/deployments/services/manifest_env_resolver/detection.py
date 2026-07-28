import json
import logging
import os
import re

logger = logging.getLogger(__name__)


class DetectionMixin:
    def _detect_stack(self) -> None:
        srcdir = self.source_dir or ""
        if self._find_file(srcdir, "manage.py") and self._find_file(srcdir, "requirements.txt"):
            self.stack = "django"
            return
        if self._find_glob(srcdir, "next.config.*") and self._find_file(srcdir, "package.json"):
            self.stack = "nextjs"
            return
        if self._find_file(srcdir, "requirements.txt") or self._find_file(srcdir, "pyproject.toml"):
            self.stack = "python"
            return
        if self._find_file(srcdir, "package.json"):
            self.stack = "node"
            pkg_path = self._find_file_path(srcdir, "package.json")
            if pkg_path:
                try:
                    with open(pkg_path, encoding="utf-8") as f:
                        pkg = json.load(f)
                    deps = {**(pkg.get("dependencies", {})), **(pkg.get("devDependencies", {}))}
                    if "next" in deps:
                        self.stack = "nextjs"
                except (json.JSONDecodeError, OSError) as exc:
                    logger.debug("Failed to parse package.json %s: %s", pkg_path, exc)
            return
        if self._find_file(srcdir, "Cargo.toml"):
            self.stack = "rust"
            return

    def _detect_port(self) -> None:
        srcdir = self.source_dir or ""
        for df in self._find_files(srcdir, "Dockerfile*"):
            try:
                with open(df, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        m = re.match(r"EXPOSE\s+(\d+)", line.strip(), re.IGNORECASE)
                        if m:
                            self.port = int(m.group(1))
                            return
            except OSError:
                continue
        if self.env_example_vars.get("PORT", "").isdigit():
            self.port = int(self.env_example_vars["PORT"])
        elif self.is_frontend or self.stack in ("nextjs", "node"):
            self.port = 3000
        else:
            self.port = 8000

    def _detect_frontend(self) -> None:
        name_lower = self.service_name.lower().replace("-", "").replace("_", "")
        if any(
            fn in name_lower for fn in ("frontend", "backoffice", "webui", "dashboard")
        ):
            self.is_frontend = True
            return
        if self.stack == "nextjs":
            srcdir = self.source_dir or ""
            has_backend = bool(
                self._find_file(srcdir, "manage.py")
                or (self._find_file(srcdir, "requirements.txt") and not self._find_file(srcdir, "package.json"))
            )
            self.is_frontend = not has_backend
