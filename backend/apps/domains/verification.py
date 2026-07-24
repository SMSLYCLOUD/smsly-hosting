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


@dataclass(frozen=True)
class DnsVerificationResult:
    """Result of checking whether a custom domain points at this platform."""

    verified: bool
    expected: str
    actual: str
    error: str = ""
    matched_by: str = ""


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


def _resolver(timeout: float = DEFAULT_RESOLVER_TIMEOUT) -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    return resolver


def _resolve_rrset(hostname: str, record_type: str, timeout: float = DEFAULT_RESOLVER_TIMEOUT):
    try:
        return _resolver(timeout).resolve(hostname, record_type)
    except (
        dns.resolver.NoAnswer,
        dns.resolver.NXDOMAIN,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
        dns.exception.DNSException,
    ):
        return []


def resolve_host_ips(hostname: str, timeout: float = DEFAULT_RESOLVER_TIMEOUT) -> set[str]:
    """Resolve A/AAAA records for a host. Falls back to libc resolution."""
    host = _clean_hostname(hostname)
    if not host:
        return set()

    ips: set[str] = set()
    for record_type in ("A", "AAAA"):
        for answer in _resolve_rrset(host, record_type, timeout=timeout):
            address = _clean_ip(getattr(answer, "address", ""))
            if address:
                ips.add(address)

    if ips:
        return ips

    try:
        for row in socket.getaddrinfo(host, 443):
            if row and len(row) >= 5 and row[4]:
                address = _clean_ip(str(row[4][0]))
                if address:
                    ips.add(address)
    except socket.gaierror:
        pass

    if not ips:
        try:
            address = _clean_ip(socket.gethostbyname(host))
            if address:
                ips.add(address)
        except socket.gaierror:
            pass

    return ips


def resolve_cname_chain(hostname: str, timeout: float = DEFAULT_RESOLVER_TIMEOUT) -> list[str]:
    """Return a CNAME chain for hostname, stopping on missing records or loops."""
    current = _clean_hostname(hostname)
    if not current:
        return []

    chain: list[str] = []
    seen = {current}
    for _ in range(MAX_CNAME_HOPS):
        answers = _resolve_rrset(current, "CNAME", timeout=timeout)
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

    cname_chain = resolve_cname_chain(domain)
    actual_ips = resolve_host_ips(domain)
    actual = _format_actual(cname_chain, actual_ips)

    cname_match = next(
        (target for target in cname_chain if target in expected_cnames),
        "",
    )
    if cname_match:
        return DnsVerificationResult(
            verified=True,
            expected=expected,
            actual=actual,
            matched_by=f"CNAME {cname_match}",
        )

    ip_matches = actual_ips & expected_ips
    if ip_matches:
        return DnsVerificationResult(
            verified=True,
            expected=expected,
            actual=actual,
            matched_by=f"IP {sorted(ip_matches)[0]}",
        )

    return DnsVerificationResult(
        verified=False,
        expected=expected,
        actual=actual,
        error=f"Expected {expected} but got {actual}.",
    )
