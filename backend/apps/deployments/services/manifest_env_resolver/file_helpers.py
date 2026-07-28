import os


class FileHelpersMixin:
    @staticmethod
    def _find_file(base_dir: str, filename: str) -> bool:
        if os.path.isfile(os.path.join(base_dir, filename)):
            return True
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    if os.path.isfile(os.path.join(subpath, filename)):
                        return True
        except OSError:
            pass
        return False

    @staticmethod
    def _find_file_path(base_dir: str, filename: str) -> str | None:
        path = os.path.join(base_dir, filename)
        if os.path.isfile(path):
            return path
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    candidate = os.path.join(subpath, filename)
                    if os.path.isfile(candidate):
                        return candidate
        except OSError:
            pass
        return None

    @staticmethod
    def _find_files(base_dir: str, pattern: str) -> list[str]:
        import glob as _glob

        results = _glob.glob(os.path.join(base_dir, pattern))
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    results.extend(_glob.glob(os.path.join(subpath, pattern)))
        except OSError:
            pass
        return results

    @staticmethod
    def _find_glob(base_dir: str, pattern: str) -> bool:
        import glob as _glob

        if _glob.glob(os.path.join(base_dir, pattern)):
            return True
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    if _glob.glob(os.path.join(subpath, pattern)):
                        return True
        except OSError:
            pass
        return False
