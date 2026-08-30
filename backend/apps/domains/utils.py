import ipaddress
import re

_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_domain(value: str, allow_wildcard: bool = False, allow_ip: bool = False) -> str:
    """Normalize a bare domain string. Rejects paths and schemes.

    For host values that may include a path (e.g. ``app.example.com/login``),
    use :func:`split_host_and_path` first so the path can be stored
    separately as ``rewrite_root`` on the host_alias.
    """
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw:
        raise ValueError("Domain is required")

    if "://" in raw:
        raise ValueError("Domain must not include a URL scheme")
    if "/" in raw or " " in raw:
        raise ValueError("Domain must not include paths or spaces")
    return _normalize_host_no_path(raw, allow_wildcard=allow_wildcard, allow_ip=allow_ip)


def split_host_and_path(value: str) -> tuple[str, str]:
    """Split ``app.example.com/login`` into ``("app.example.com", "/login")``.

    Trailing slashes on the path are normalized to a single leading slash.
    If there's no path, the second element is the empty string.

    Only the host portion is lowercased (hosts are case-insensitive);
    the path preserves case (URL paths are case-sensitive).
    """
    raw = str(value or "").strip()
    if "://" in raw:
        raise ValueError("Host must not include a URL scheme")
    if " " in raw:
        raise ValueError("Host must not include spaces")
    if "/" not in raw:
        return raw.lower(), ""
    host, _, path = raw.partition("/")
    host = host.strip().lower().rstrip(".")
    if not host:
        raise ValueError("Host part is required before the path")
    path = "/" + path.lstrip("/") if path else "/"
    return host, path


def _normalize_host_no_path(raw: str, allow_wildcard: bool = False, allow_ip: bool = False) -> str:
    wildcard = False
    domain = raw
    if raw.startswith("*."):
        if not allow_wildcard:
            raise ValueError("Wildcard domains are not allowed")
        wildcard = True
        domain = raw[2:]

    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid internationalized domain name") from exc

    if len(ascii_domain) > 253:
        raise ValueError("Domain is too long")

    try:
        ipaddress.ip_address(ascii_domain)
        if not allow_ip:
            raise ValueError("IP addresses are not valid custom domains")
        return ascii_domain
    except ValueError as exc:
        if str(exc) == "IP addresses are not valid custom domains":
            raise

    labels = ascii_domain.split(".")
    if len(labels) < 2:
        raise ValueError("Domain must include a top-level domain")

    for label in labels:
        if not _LABEL_RE.match(label):
            raise ValueError("Domain label contains invalid characters")

    return f"*.{ascii_domain}" if wildcard else ascii_domain
