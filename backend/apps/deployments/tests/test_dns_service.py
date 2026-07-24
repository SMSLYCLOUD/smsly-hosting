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
