"""Unit tests for the CrowdSec auto-unblock sweeper (no DB, no docker)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.crowdsec.tasks import _auto_unblock_config, crowdsec_auto_unblock


def _decision(value="1.2.3.4", scope="Ip", age_hours=30, simulated=False,
              dec_type="ban"):
    return SimpleNamespace(
        id="d1", value=value, scope=scope, type=dec_type,
        simulated=simulated,
        start_time=(datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat(),
        first_seen="",
    )


def _run(decisions, enabled=True, hours=24):
    mock_service = MagicMock()
    mock_service.get_decisions.return_value = decisions
    mock_service.unban.return_value = {"status": "removed"}
    with patch(
        "apps.crowdsec.tasks._auto_unblock_config", return_value=(enabled, hours)
    ), patch(
        "apps.crowdsec.services.get_crowdsec_service", return_value=mock_service
    ):
        return crowdsec_auto_unblock.run(), mock_service


class TestAutoUnblockConfig(TestCase):
    def test_defaults_when_unreadable(self):
        with patch(
            "apps.deployments.models.PlatformConfig.load",
            side_effect=RuntimeError("no db"),
        ):
            self.assertEqual(_auto_unblock_config(), (True, 24))

    def test_floor_and_enabled_passthrough(self):
        config = SimpleNamespace(
            crowdsec_auto_unblock_enabled=False,
            crowdsec_auto_unblock_after_hours=0,
        )
        with patch(
            "apps.deployments.models.PlatformConfig.load", return_value=config
        ):
            enabled, hours = _auto_unblock_config()
        self.assertFalse(enabled)
        self.assertEqual(hours, 1)


class TestCrowdsecAutoUnblock(TestCase):
    def test_disabled_mode_touches_nothing(self):
        res, mock_service = _run([_decision()], enabled=False)
        self.assertEqual(res["mode"], "disabled")
        self.assertEqual(res["unbanned"], 0)
        mock_service.unban.assert_not_called()

    def test_old_ban_removed(self):
        res, mock_service = _run([_decision(age_hours=30)], hours=24)
        self.assertEqual(res["unbanned"], 1)
        mock_service.unban.assert_called_once_with("1.2.3.4", "Ip")

    def test_fresh_ban_kept(self):
        res, mock_service = _run([_decision(age_hours=2)], hours=24)
        self.assertEqual(res["unbanned"], 0)
        mock_service.unban.assert_not_called()

    def test_simulated_never_touched(self):
        res, mock_service = _run(
            [_decision(age_hours=100, simulated=True)], hours=24
        )
        self.assertEqual(res["unbanned"], 0)
        mock_service.unban.assert_not_called()

    def test_unknown_scope_never_touched(self):
        res, mock_service = _run(
            [_decision(age_hours=100, scope="")], hours=24
        )
        self.assertEqual(res["unbanned"], 0)
        mock_service.unban.assert_not_called()

    def test_range_uses_range_type(self):
        res, mock_service = _run(
            [_decision(value="10.0.0.0/24", scope="Range", age_hours=100)],
            hours=24,
        )
        self.assertEqual(res["unbanned"], 1)
        mock_service.unban.assert_called_once_with("10.0.0.0/24", "Range")

    def test_unban_failure_counted(self):
        mock_service = MagicMock()
        mock_service.get_decisions.return_value = [_decision(age_hours=100)]
        mock_service.unban.return_value = {"error": "daemon down"}
        with patch(
            "apps.crowdsec.tasks._auto_unblock_config", return_value=(True, 24)
        ), patch(
            "apps.crowdsec.services.get_crowdsec_service", return_value=mock_service
        ):
            res = crowdsec_auto_unblock.run()
        self.assertEqual(res["failed"], 1)
        self.assertEqual(res["unbanned"], 0)
