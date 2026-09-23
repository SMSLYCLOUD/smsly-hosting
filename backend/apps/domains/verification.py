"""DNS verification helpers for custom service domains."""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

import dns.exception
import dns.resolver
from .utils import normalize_domain

DEFAULT_RESOLVER_TIMEOUT = 2.0
MAX_CNAME_HOPS = 10

# ── Anti-rebinding: verify via INDEPENDENT public resolvers ──────────────
# The old code used a bare dns.resolver.Resolver() — the system resolver
# inside the backend container (Docker embedded DNS -> host). Two attacks
# worked against that:
#   1. DNS REBINDING: an attacker's authoritative NS answers with the
#      platform IP for our verification query, then serves a different
#      IP to real users (or flips records right after verification —
#      the sticky `verified` flag kept the cert + routing alive).
#   2. FAKE-IP ROUND-ROBIN: the attacker's domain alternates the
#      platform IP and their own server across queries; one lucky
#      lookup passes verification.
# Mitigation: every verification must hold through a QUORUM of
# independent resolvers (Cloudflare, Google, Quad9) and the libc
# fallback is removed from the verification path entirely (a
# container-local /etc/hosts or search-domain can poison it).
# When the platform is Edge-Shield-proxied, the ONLY acceptable direct
# answer is a CNAME into the platform (proxied A records return
# Cloudflare edge IPs — shared with every other Cloudflare customer,
# so an A-match there would let anyone's proxied domain verify).
PUBLIC_VERIFICATION_RESOLVERS: tuple[str, ...] = (
    "1.1.1.1",
    "8.8.8.8",
    "9.9.9.9",
)
VERIFICATION_QUORUM = 2  # of len(PUBLIC_VERIFICATION_RESOLVERS)


@dataclass(frozen=True)
class DnsVerificationResult:
    """Result of checking whether a custom domain points at this platform."""

    verified: bool
    expected: str
    actual: str
    error: str = ""
    matched_by: str = ""


def ensure_verification_token(domain_obj) -> str:
    """Return the row's HTTP-proof token, creating one on first use.

    Called by the verify tasks (never by the pure DNS check itself).
    Fail-open: returns "" when the row cannot be updated.
    """
    import secrets
    token = str(getattr(domain_obj, "verification_token", "") or "").strip()
    if not token:
        token = secrets.token_urlsafe(32)
        try:
            domain_obj.verification_token = token
            domain_obj.save(update_fields=["verification_token"])
        except Exception:
            pass
    return token or ""


def verify_http_proof(domain_obj, timeout: float = 10) -> tuple:
    """Fetch the row's challenge token over the PUBLIC edge.

    Works grey AND orange-proxied (and through apex CNAME flattening),
    where DNS-quorum is structurally blind: Cloudflare serves edge IPs
    / NoAnswer instead of the chain. Strength equals DNS proof — only
    whoever steers the hostname at us can complete it — plus path proof
    that traffic actually arrives. Returns (ok, detail).
    """
    host = _clean_hostname(getattr(domain_obj, "domain_name", "") or "")
    token = str(getattr(domain_obj, "verification_token", "") or "").strip()
    if not host or not token:
        return False, "no token"
    try:
        import requests
        resp = requests.get(
            f"http://{host}/.well-known/smsly-verify/{token}",
            timeout=timeout, allow_redirects=True,
            headers={"User-Agent": "smsly-domain-verify/1.0"},
        )
    except Exception as exc:
        return False, f"fetch failed: {exc!s}"[:160]
    if resp.status_code == 200 and (resp.text or "").strip() == token:
        return True, "served token over public edge"
    return False, f"HTTP {resp.status_code}"


def _clean_hostname(value: str) -> str:
    """Return a normalized hostname-ish value, or an empty string."""
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw:
        return ""
    try:
        return normalize_domain(raw)
    except ValueError:
        return raw


def _clean_ip(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return ""


def _resolver(timeout: float = DEFAULT_RESOLVER_TIMEOUT,
              nameservers: tuple[str, ...] | None = None) -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver(configure=False)
    resolver.timeout = timeout
    resolver.lifetime = timeout
    if nameservers:
        resolver.nameservers = list(nameservers)
    else:
        # Verification path must NEVER use the system resolver — only
        # the explicit public set. (Callers wanting local resolution
        # pass nameservers explicitly.)
        resolver.nameservers = list(PUBLIC_VERIFICATION_RESOLVERS)
    return resolver


def _resolve_rrset(hostname: str, record_type: str,
                    timeout: float = DEFAULT_RESOLVER_TIMEOUT,
                    nameservers: tuple[str, ...] | None = None):
    try:
        return _resolver(timeout, nameservers).resolve(hostname, record_type)
    except (
        dns.resolver.NoAnswer,
        dns.resolver.NXDOMAIN,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
        dns.exception.DNSException,
    ):
        return []


def resolve_host_ips(hostname: str, timeout: float = DEFAULT_RESOLVER_TIMEOUT,
                     nameservers: tuple[str, ...] | None = None) -> set[str]:
    """Resolve A/AAAA records for a host via the PUBLIC resolver set.

    The old libc fallbacks (socket.getaddrinfo / gethostbyname) were the
    rebinding vector — they consult /etc/hosts and the container's
    DNS config, which an attacker with any foothold (or a poisoned
    search domain) can steer. Verification now resolves only through
    the explicit public resolvers.
    """
    host = _clean_hostname(hostname)
    if not host:
        return set()

    ips: set[str] = set()
    for record_type in ("A", "AAAA"):
        for answer in _resolve_rrset(host, record_type, timeout=timeout,
                                     nameservers=nameservers):
            address = _clean_ip(getattr(answer, "address", ""))
            if address:
                ips.add(address)
    return ips


def resolve_cname_chain(hostname: str, timeout: float = DEFAULT_RESOLVER_TIMEOUT,
                        nameservers: tuple[str, ...] | None = None) -> list[str]:
    """Return a CNAME chain for hostname, stopping on missing records or loops."""
    current = _clean_hostname(hostname)
    if not current:
        return []

    chain: list[str] = []
    seen = {current}
    for _ in range(MAX_CNAME_HOPS):
        answers = _resolve_rrset(current, "CNAME", timeout=timeout,
                                 nameservers=nameservers)
        if not answers:
            break
        target = _clean_hostname(str(getattr(answers[0], "target", answers[0])))
        if not target or target in seen:
            break
        chain.append(target)
        seen.add(target)
        current = target

    return chain


def _format_expected(cnames: set[str], ips: set[str]) -> str:
    parts = []
    if cnames:
        parts.append("CNAME to " + " or ".join(sorted(cnames)))
    if ips:
        parts.append("A/AAAA record to " + " or ".join(sorted(ips)))
    return " or ".join(parts) if parts else "platform CNAME or server IP"


def _format_actual(cnames: list[str], ips: set[str]) -> str:
    parts = []
    if cnames:
        parts.append("CNAME chain " + " -> ".join(cnames))
    if ips:
        parts.append("A/AAAA records " + ", ".join(sorted(ips)))
    return "; ".join(parts) if parts else "No CNAME, A, or AAAA records found"


def _expected_targets(domain_obj, config) -> tuple[set[str], set[str]]:
    cnames: set[str] = set()
    ips: set[str] = set()

    service = getattr(domain_obj, "service", None)
    service_domain = _clean_hostname(getattr(service, "public_domain", "") or "")
    if service_domain:
        cnames.add(service_domain)

    platform_domain = _clean_hostname(getattr(config, "domain", "") or "")
    if platform_domain:
        cnames.add(platform_domain)

    server_ip = _clean_ip(getattr(config, "server_ip", "") or "")
    if server_ip:
        ips.add(server_ip)

    if not ips:
        for target in cnames:
            ips.update(resolve_host_ips(target))

    return cnames, ips


def verify_custom_domain_dns(domain_obj, config) -> DnsVerificationResult:
    """
    Check that domain_obj points at either the service/platform CNAME or server IP.

    This intentionally does not call any DNS provider API. It validates the
    public DNS state needed for direct ACME HTTP/TLS-ALPN certificate issuance.

    Anti-rebinding: the domain must verify through a QUORUM of independent
    public resolvers. A single lying answer (rebind, round-robin fake IP)
    cannot pass; the attacker would have to control the global resolution
    of their domain for the duration — which is the same as controlling the
    domain, the legitimate ownership model.

    Edge-Shield interaction: when the platform's records are
    Cloudflare-proxied, `resolve_host_ips(platform_domain)` returns CF
    EDGE IPs — shared by every Cloudflare customer. Matching an
    attacker's A record against those would let anyone's proxied domain
    "verify" against us. So the IP-match only accepts the platform's
    ORIGIN IP; CNAME matches (the recommended path) resolve through
    the chain and are quorum-checked.
    """
    domain = _clean_hostname(getattr(domain_obj, "domain_name", "") or "")
    if not domain:
        return DnsVerificationResult(
            verified=False,
            expected="valid custom domain",
            actual="Invalid domain",
            error="Invalid custom domain",
        )

    expected_cnames, expected_ips = _expected_targets(domain_obj, config)
    expected = _format_expected(expected_cnames, expected_ips)
    if not expected_cnames and not expected_ips:
        return DnsVerificationResult(
            verified=False,
            expected=expected,
            actual="No platform DNS target configured",
            error="Set a service public domain, platform domain, or server IP before verifying custom domains.",
        )

    # Origin IP only — never the resolved edge IPs of a proxied platform
    # domain (see docstring). _expected_targets may have resolved
    # platform-domain A records into expected_ips when server_ip was
    # unset; those are edge IPs when proxied and MUST NOT be accepted
    # for a customer's A record.
    origin_ip = _clean_ip(getattr(config, "server_ip", "") or "")
    acceptable_ips = {origin_ip} if origin_ip else set()

    # ── Quorum verification across independent resolvers ──────────────
    # For each public resolver, resolve the domain independently and
    # evaluate the match. The domain verifies only if at least
    # VERIFICATION_QUORUM resolvers agree it points at us.
    agreeing = 0
    first_match = ""
    sample_chain: list[str] = []
    sample_ips: set[str] = set()

    for ns in PUBLIC_VERIFICATION_RESOLVERS:
        chain = resolve_cname_chain(domain, nameservers=(ns,))
        ips = resolve_host_ips(domain, nameservers=(ns,))
        if not sample_chain and chain:
            sample_chain = chain
        sample_ips.update(ips)

        matched = ""
        for target in chain:
            if target in expected_cnames:
                matched = f"CNAME {target}"
                break
        if not matched and acceptable_ips:
            ip_hits = ips & acceptable_ips
            if ip_hits:
                matched = f"IP {sorted(ip_hits)[0]}"
        if matched:
            agreeing += 1
            if not first_match:
                first_match = matched

    actual = _format_actual(sample_chain, sample_ips)
    if agreeing >= VERIFICATION_QUORUM:
        return DnsVerificationResult(
            verified=True,
            expected=expected,
            actual=actual,
            matched_by=f"{first_match} (verified via {agreeing}/{len(PUBLIC_VERIFICATION_RESOLVERS)} resolvers)",
        )

    # HTTP-proof fallback (orange-compatible): DNS quorum above cannot see
    # through Cloudflare proxying or apex CNAME flattening. Fetching the
    # row's token over the public edge proves the same control.
    http_ok, http_detail = verify_http_proof(domain_obj)
    if http_ok:
        return DnsVerificationResult(
            verified=True,
            expected=expected,
            actual=f"HTTP proof: {http_detail}",
            matched_by=f"HTTP proof ({http_detail})",
        )

    if agreeing == 1:
        return DnsVerificationResult(
            verified=False,
            expected=expected,
            actual=actual,
            error=(
                "Domain points at this platform on only one resolver — "
                "possible DNS rebinding or propagation lag. Point DNS "
                "consistently and retry in a few minutes."
            ),
        )

    return DnsVerificationResult(
        verified=False,
        expected=expected,
        actual=actual,
        error=f"Expected {expected} but got {actual} (checked via {len(PUBLIC_VERIFICATION_RESOLVERS)} independent resolvers; HTTP proof: {http_detail}).",
    )
