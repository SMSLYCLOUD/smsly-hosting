import ipaddress
import re


class KeyGenMixin:

    INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")

    @classmethod
    def validate_interface_name(cls, iface: str) -> str:
        value = str(iface or "").strip()
        if not cls.INTERFACE_RE.fullmatch(value):
            raise ValueError("Invalid WireGuard interface name.")
        return value

    @staticmethod
    def validate_endpoint(endpoint: str) -> str:
        value = str(endpoint or "").strip()
        if not value:
            return ""
        if value.count(":") < 1:
            raise ValueError("WireGuard endpoint must include a port.")
        host, port_raw = value.rsplit(":", 1)
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ValueError("WireGuard endpoint port must be numeric.") from exc
        if port < 1 or port > 65535:
            raise ValueError("WireGuard endpoint port is out of range.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
                raise ValueError("WireGuard endpoint host is invalid.")
        return f"{host}:{port}"

    @staticmethod
    def validate_wg_config(config: str) -> None:
        if not isinstance(config, str) or "\x00" in config:
            raise ValueError("Invalid WireGuard config.")
        required_patterns = [
            r"(?m)^\s*\[Interface\]\s*$",
            r"(?m)^\s*PrivateKey\s*=\s*\S+",
            r"(?m)^\s*Address\s*=\s*\S+",
        ]
        for pattern in required_patterns:
            if not re.search(pattern, config):
                raise ValueError("WireGuard config is missing required interface fields.")

    @staticmethod
    def generate_keypair() -> tuple[str, str]:
        return KeyGenMixin._generate_keypair_python()

    @staticmethod
    def _generate_keypair_python() -> tuple[str, str]:
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        private_key = X25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return (
            base64.b64encode(private_bytes).decode(),
            base64.b64encode(public_bytes).decode(),
        )
