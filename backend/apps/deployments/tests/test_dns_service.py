from apps.domains.services import dns


def test_ensure_dns_records_updates_proxied_record_to_dns_only(monkeypatch):
    monkeypatch.setattr(dns, "_get_zone_id", lambda token, zone_name: "zone-1")

    def fake_get_records(token, zone_id, name, record_type):
        if record_type == "A":
            return [{"id": "record-1", "content": "153.75.247.117", "proxied": True}]
        return []

    updates = []

    def fake_update_record(token, zone_id, record_id, name, content, proxied=False):
        updates.append(
            {
                "record_id": record_id,
                "name": name,
                "content": content,
                "proxied": proxied,
            }
        )
        return True, "updated"

    monkeypatch.setattr(dns, "_get_records", fake_get_records)
    monkeypatch.setattr(dns, "_update_record", fake_update_record)

    result = dns.ensure_dns_records(
        ["smsly-frontend-0b774a.cloud.smsly.cloud"],
        "153.75.247.117",
        "token",
    )

    assert result["ok"] is True
    assert result["updated"] == ["smsly-frontend-0b774a.cloud.smsly.cloud"]
    assert updates == [
        {
            "record_id": "record-1",
            "name": "smsly-frontend-0b774a.cloud.smsly.cloud",
            "content": "153.75.247.117",
            "proxied": False,
        }
    ]


def test_ensure_dns_records_creates_dns_only_record(monkeypatch):
    monkeypatch.setattr(dns, "_get_zone_id", lambda token, zone_name: "zone-1")
    monkeypatch.setattr(dns, "_get_records", lambda token, zone_id, name, record_type: [])

    created = []

    def fake_create_record(token, zone_id, name, content, proxied=False):
        created.append({"name": name, "content": content, "proxied": proxied})
        return True, "created"

    monkeypatch.setattr(dns, "_create_record", fake_create_record)

    result = dns.ensure_dns_records(["ignite.smsly.cloud"], "153.75.247.117", "token")

    assert result["ok"] is True
    assert result["created"] == ["ignite.smsly.cloud"]
    assert created == [{"name": "ignite.smsly.cloud", "content": "153.75.247.117", "proxied": False}]


class _FakePlatformConfig:
    def __init__(self, domain="", edge_proxy_records=False, edge_proxy_wildcards=False):
        self.domain = domain
        self.edge_proxy_records = edge_proxy_records
        self.edge_proxy_wildcards = edge_proxy_wildcards


def _run_ensure(monkeypatch, domains, cfg):
    monkeypatch.setattr(
        "apps.deployments.models.PlatformConfig.load",
        classmethod(lambda cls: cfg),
    )
    monkeypatch.setattr(dns, "_get_zone_id", lambda token, zone_name: "zone-1")
    monkeypatch.setattr(dns, "_get_records", lambda token, zone_id, name, record_type: [])
    created = []

    def fake_create_record(token, zone_id, name, content, proxied=False):
        created.append({"name": name, "content": content, "proxied": proxied})
        return True, "created"

    monkeypatch.setattr(dns, "_create_record", fake_create_record)
    result = dns.ensure_dns_records(domains, "176.31.201.181", "token")
    assert result["ok"] is True
    return created


def test_service_hostname_follows_wildcard_flag_not_shield(monkeypatch):
    cfg = _FakePlatformConfig(
        domain="grid.smsly.cloud", edge_proxy_records=True, edge_proxy_wildcards=False
    )
    created = _run_ensure(monkeypatch, ["svc-1.grid.smsly.cloud"], cfg)
    assert created == [
        {"name": "svc-1.grid.smsly.cloud", "content": "176.31.201.181", "proxied": False}
    ]


def test_service_hostname_proxied_when_wildcards_opted_in(monkeypatch):
    cfg = _FakePlatformConfig(
        domain="grid.smsly.cloud", edge_proxy_records=True, edge_proxy_wildcards=True
    )
    created = _run_ensure(monkeypatch, ["svc-1.grid.smsly.cloud"], cfg)
    assert created == [
        {"name": "svc-1.grid.smsly.cloud", "content": "176.31.201.181", "proxied": True}
    ]


def test_deeper_than_wildcard_always_dns_only(monkeypatch):
    cfg = _FakePlatformConfig(
        domain="grid.smsly.cloud", edge_proxy_records=True, edge_proxy_wildcards=True
    )
    created = _run_ensure(monkeypatch, ["a.b.grid.smsly.cloud"], cfg)
    assert created == [
        {"name": "a.b.grid.smsly.cloud", "content": "176.31.201.181", "proxied": False}
    ]


def test_platform_apex_still_follows_shield_flag(monkeypatch):
    cfg = _FakePlatformConfig(
        domain="grid.smsly.cloud", edge_proxy_records=True, edge_proxy_wildcards=False
    )
    created = _run_ensure(monkeypatch, ["grid.smsly.cloud"], cfg)
    assert created == [
        {"name": "grid.smsly.cloud", "content": "176.31.201.181", "proxied": True}
    ]


def test_unrelated_domain_unchanged(monkeypatch):
    cfg = _FakePlatformConfig(
        domain="grid.smsly.cloud", edge_proxy_records=True, edge_proxy_wildcards=False
    )
    created = _run_ensure(monkeypatch, ["other.example.com"], cfg)
    assert created == [
        {"name": "other.example.com", "content": "176.31.201.181", "proxied": True}
    ]


def test_wildcard_record_follows_flag(monkeypatch):
    cfg = _FakePlatformConfig(
        domain="grid.smsly.cloud", edge_proxy_records=True, edge_proxy_wildcards=False
    )
    created = _run_ensure(monkeypatch, ["*.grid.smsly.cloud"], cfg)
    assert created == [
        {"name": "*.grid.smsly.cloud", "content": "176.31.201.181", "proxied": False}
    ]
