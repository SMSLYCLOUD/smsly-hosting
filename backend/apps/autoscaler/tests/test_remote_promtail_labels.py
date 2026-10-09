"""Remote Promtail label parity tests.

The SSH-deployed remote Promtail (see
``apps.autoscaler.services.prometheus_targets._generate_remote_promtail_config``)
ships node container logs to the central Loki. Its docker_sd relabel set
must stay at parity with the master
``infrastructure/monitoring/promtail-config.yml`` docker job, otherwise
node-shipped logs lose the ownership labels tenant scoping depends on
(2026-10: smsly_service_id / smsly_project_id / smsly_project were missing
and the keep filter dropped compose-deployed platform containers).
"""
from pathlib import Path

import yaml
from django.test import SimpleTestCase

from apps.autoscaler.services.prometheus_targets import (
    _generate_remote_promtail_config,
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infrastructure" / "monitoring" / "promtail-config.yml").exists():
            return parent
    raise AssertionError("repo root with infrastructure/monitoring not found")


def _docker_job(config: dict) -> dict:
    for job in config.get("scrape_configs", []):
        if job.get("job_name") == "docker":
            return job
    raise AssertionError("no docker job in scrape_configs")


class RemotePromtailLabelTests(SimpleTestCase):
    def test_remote_config_is_valid_yaml_with_docker_job(self):
        config = yaml.safe_load(
            _generate_remote_promtail_config("http://10.100.0.1:3100/loki/api/v1/push")
        )
        job = _docker_job(config)
        self.assertTrue(job.get("docker_sd_configs"))
        self.assertTrue(job.get("relabel_configs"))

    def test_remote_config_carries_smsly_ownership_labels(self):
        """Node-shipped logs must carry service/project labels for scoping."""
        config = yaml.safe_load(_generate_remote_promtail_config("http://x:3100/x"))
        targets = {
            rule.get("target_label")
            for rule in _docker_job(config).get("relabel_configs", [])
            if rule.get("target_label")
        }
        for label in (
            "compose_service",
            "compose_project",
            "container_name",
            "container",
            "smsly_service_id",
            "smsly_project_id",
            "smsly_project",
        ):
            self.assertIn(label, targets)

    def test_remote_keep_covers_compose_and_managed_by(self):
        """Compose-deployed platform containers on the node must be kept."""
        config = yaml.safe_load(_generate_remote_promtail_config("http://x:3100/x"))
        keeps = [
            rule
            for rule in _docker_job(config).get("relabel_configs", [])
            if rule.get("action") == "keep"
        ]
        self.assertTrue(keeps)
        sources = keeps[0].get("source_labels", [])
        self.assertIn(
            "__meta_docker_container_label_com_docker_compose_project", sources
        )
        self.assertIn("__meta_docker_container_label_managed_by", sources)

    def test_remote_container_label_guarded_and_empty_dropped(self):
        """Empty container IDs must neither blank the label nor ship."""
        config = yaml.safe_load(_generate_remote_promtail_config("http://x:3100/x"))
        job = _docker_job(config)
        container_rules = [
            rule
            for rule in job.get("relabel_configs", [])
            if rule.get("target_label") == "container"
        ]
        self.assertTrue(container_rules)
        for rule in container_rules:
            self.assertEqual(rule.get("regex"), "(.+)")
        stages = job.get("pipeline_stages", [])
        drops = [
            stage.get("match", {})
            for stage in stages
            if isinstance(stage, dict) and "match" in stage
        ]
        selectors = [match.get("selector") for match in drops]
        self.assertIn('{container=""}', selectors)

    def test_remote_relabels_at_parity_with_master(self):
        """Every target_label the master docker job sets, remote must set."""
        master_path = (
            _repo_root() / "infrastructure" / "monitoring" / "promtail-config.yml"
        )
        master = yaml.safe_load(master_path.read_text(encoding="utf-8"))
        remote = yaml.safe_load(_generate_remote_promtail_config("http://x:3100/x"))
        master_targets = {
            rule.get("target_label")
            for rule in _docker_job(master).get("relabel_configs", [])
            if rule.get("target_label")
        }
        remote_targets = {
            rule.get("target_label")
            for rule in _docker_job(remote).get("relabel_configs", [])
            if rule.get("target_label")
        }
        self.assertTrue(master_targets)
        self.assertEqual(master_targets - remote_targets, set())
