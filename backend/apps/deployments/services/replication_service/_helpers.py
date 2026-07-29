class HelpersMixin:

    @staticmethod
    def _parse_lsn(lsn_str: str) -> int:
        if not isinstance(lsn_str, str) or not lsn_str:
            return 0
        parts = lsn_str.split("/")
        if len(parts) == 2:
            try:
                return int(parts[0], 16) * (2 ** 32) + int(parts[1], 16)
            except (ValueError, TypeError):
                return 0
        return 0
