"""Buildx-first build path (real BuildKit) with classic fallback.

Subprocess + Docker are fully mocked. Covers command construction
(--load/--secret/--cache-from/--cache-to), secret-file hygiene,
infra-only fallback, Dockerfile-error passthrough, engine selection,
and the recovery degraded-cache skip.
"""
import os
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

import apps.deployments.services.pipeline.build as build_mod
from apps.deployments.services.pipeline.build import (
    _cleanup_secret_files,
    _is_buildx_infra_error,
    _stage_secret_files,
    _use_buildx,
)


def _mixin():
    m = build_mod.BuildMixin()
    m.deployment = SimpleNamespace()
    m.secret_values = []
    m.append_log_calls = []
    return m


def _ok_proc():
    return SimpleNamespace(returncode=0, stdout="Step 1/2 : FROM x\n", stderr="")


class BuildxCommandTests(TestCase):
    def _run_buildx(self, dockerfile_text="FROM x\n", **kw):
        mixin = _mixin()
        with patch.object(
            build_mod, "append_log",
            side_effect=lambda dep, msg: mixin.append_log_calls.append(msg),
        ), patch("subprocess.run", return_value=_ok_proc()) as mock_run, \
                patch("builtins.open",
                      MagicMock(return_value=MagicMock(
                          __enter__=MagicMock(return_value=MagicMock(
                              read=MagicMock(return_value=dockerfile_text))),
                          __exit__=MagicMock(return_value=False)))):
            # bind the unbound method to our stub
            build_mod.BuildMixin._build_with_buildkit(
                mixin, context_dir="/tmp/ctx", dockerfile_path="/tmp/ctx/Dockerfile",
                tag="registry:5000/smsly/a:aaa1111", buildargs={"A": "b"},
                cache_from=["registry:5000/smsly/a:aaa1111"],
                secrets={"github_token": "sekret"},
                **kw,
            )
        return mock_run

    def test_buildkit_flags_and_secret_mount(self):
        mock_run = self._run_buildx()
        cmd = mock_run.call_args.args[0]
        # A systemd-run resource scope may prefix the command; find docker.
        start = cmd.index('docker')
        cmd = cmd[start:]
        # Plain CLI: no buildx, no builder selection, no --load flag.
        self.assertEqual(cmd[:3], ["docker", "build", "--progress=plain"])
        self.assertNotIn("buildx", cmd)
        self.assertNotIn("--builder", cmd)
        self.assertIn("--progress=plain", cmd)
        self.assertIn("registry:5000/smsly/a:aaa1111", cmd)
        secret_flags = [cmd[i + 1] for i, a in enumerate(cmd[:-1])
                        if a == "--secret"]
        self.assertEqual(len(secret_flags), 1)
        self.assertTrue(secret_flags[0].startswith("id=github_token,src="))
        self.assertIn("--cache-to", cmd)
        self.assertIn("type=inline", cmd)
        self.assertIn("type=registry,ref=registry:5000/smsly/a:aaa1111", cmd)

    def test_secret_aware_dockerfile_skips_arg_fallback(self):
        mock_run = self._run_buildx(
            dockerfile_text="FROM x\nRUN --mount=type=secret,id=github_token cat\n")
        cmd = mock_run.call_args.args[0]
        args = [cmd[i + 1] for i, a in enumerate(cmd[:-1])
                if a == "--build-arg"]
        self.assertIn("A=b", args)  # non-secret buildargs still pass
        self.assertFalse(any(a.startswith("github_token=")
                             for a in args))

    def test_secret_unaware_dockerfile_keeps_arg_compat(self):
        mixin_calls = []
        mixin = _mixin()
        with patch.object(
            build_mod, "append_log",
            side_effect=lambda dep, msg: mixin_calls.append(msg),
        ), patch("subprocess.run", return_value=_ok_proc()) as mock_run:
            build_mod.BuildMixin._build_with_buildkit(
                mixin, context_dir="/tmp/ctx",
                dockerfile_path="/nonexistent/Dockerfile",
                tag="t", buildargs={}, cache_from=[],
                secrets={"github_token": "sekret"},
            )
        cmd = mock_run.call_args.args[0]
        args = [cmd[i + 1] for i, a in enumerate(cmd[:-1])
                if a == "--build-arg"]
        self.assertIn("github_token=sekret", args)
        self.assertTrue(any("WARNING" in m for m in mixin_calls))


class BuildxFallbackTests(TestCase):
    def _engine(self, buildx_exc=None):
        mixin = _mixin()
        with patch.object(
            build_mod, "append_log",
            side_effect=lambda dep, msg: mixin.append_log_calls.append(msg),
        ), patch.object(
            build_mod, "_use_buildx", return_value=True,
        ), patch.object(
            build_mod.BuildMixin, "_build_with_buildkit",
            side_effect=buildx_exc,
        ) as mock_bx, patch.object(
            build_mod.BuildMixin, "_build_via_docker_py",
            return_value=None,
        ) as mock_classic:
            from apps.deployments.services.pipeline.exceptions import (
                BuildError,
            )
            try:
                build_mod.BuildMixin._build_with_docker_engine(
                    mixin, context_dir="/tmp/ctx",
                    dockerfile_path="/tmp/ctx/Dockerfile",
                    image_name="t", build_args_dict={}, cache_from=[],
                    build_secrets={},
                )
                return ("ok", mock_bx, mock_classic, None)
            except BuildError as exc:
                return ("builderror", mock_bx, mock_classic, exc)

    def test_infra_error_falls_back_to_classic(self):
        status, mock_bx, mock_classic, _ = self._engine(
            OSError("cannot connect to the Docker daemon"))
        self.assertEqual(status, "ok")
        self.assertTrue(mock_bx.called)
        self.assertTrue(mock_classic.called)

    def test_dockerfile_error_never_falls_back(self):
        from apps.deployments.services.pipeline.exceptions import BuildError
        status, mock_bx, mock_classic, exc = self._engine(
            BuildError("Docker build failed: COPY failed: not found"))
        self.assertEqual(status, "builderror")
        self.assertTrue(mock_bx.called)
        self.assertFalse(mock_classic.called)

    def test_classic_forced_by_env(self):
        with patch.dict(os.environ, {"SMSLY_DOCKER_BUILDER": "classic"}):
            self.assertFalse(_use_buildx())
        with patch.dict(os.environ, {"SMSLY_DOCKER_BUILDER": "buildx"}):
            self.assertTrue(_use_buildx())

    def test_infra_classifier(self):
        for text in ("docker: command not found",
                     "Cannot connect to the Docker daemon",
                     "error during connect: Head",
                     "failed to dial buildkitd",
                     "no builder currently"):
            self.assertTrue(_is_buildx_infra_error(text), text)
        for text in ("COPY failed: file not found",
                     "failed to export layer: CreateDiff",
                     "executor failed running [/bin/sh]"):
            self.assertFalse(_is_buildx_infra_error(text), text)


class PlainCliBuildTests(TestCase):
    """Daemon BuildKit via the plain CLI carries no builder selection.

    Regression (2026-09-24): a phantom docker-container `default` builder
    (plus a refused `create --driver docker`) silently downgraded every
    build to classic. The plain `DOCKER_BUILDKIT=1 docker build` path has
    no client state to rot: no buildx, no --builder, no --load flag.
    """

    def test_plain_cli_has_no_builder_state(self):
        mixin_calls = []
        mixin = _mixin()
        with patch.object(
            build_mod, "append_log",
            side_effect=lambda dep, msg: mixin_calls.append(msg),
        ), patch("subprocess.run",
                 return_value=_ok_proc()) as mock_run, \
                patch("builtins.open",
                      MagicMock(return_value=MagicMock(
                          __enter__=MagicMock(return_value=MagicMock(
                              read=MagicMock(return_value="FROM x\n"))),
                          __exit__=MagicMock(return_value=False)))):
            build_mod.BuildMixin._build_with_buildkit(
                mixin, context_dir="/tmp/ctx",
                dockerfile_path="/tmp/ctx/Dockerfile",
                tag="registry:5000/smsly/a:aaa1111", buildargs={"A": "b"},
                cache_from=["registry:5000/smsly/a:aaa1111"],
                secrets={"github_token": "sekret"},
            )
        cmd = mock_run.call_args.args[0]
        start = cmd.index('docker')
        cmd = cmd[start:]
        self.assertEqual(cmd[:3], ["docker", "build", "--progress=plain"])
        self.assertNotIn("buildx", cmd)
        self.assertNotIn("--builder", cmd)
        self.assertNotIn("--load", cmd)
        env = mock_run.call_args.kwargs.get("env", {})
        self.assertEqual(env.get("DOCKER_BUILDKIT"), "1")


class SecretFileHygieneTests(TestCase):
    def test_stage_and_cleanup_roundtrip(self):
        ids, paths = _stage_secret_files({"github_token": "sekret"})
        try:
            self.assertEqual(ids, ["github_token"])
            self.assertEqual(len(paths), 1)
            if os.name != "nt":
                # POSIX permission bits aren't meaningful on Windows.
                mode = stat.S_IMODE(os.stat(paths[0]).st_mode)
                self.assertEqual(mode, 0o600)
            with open(paths[0]) as f:
                self.assertEqual(f.read(), "sekret")
        finally:
            _cleanup_secret_files(paths)
        import os as _os
        self.assertFalse(_os.path.exists(paths[0]))

    def test_cleanup_never_raises(self):
        _cleanup_secret_files(None)
        _cleanup_secret_files([])
        _cleanup_secret_files(["/nonexistent/smsly-secret-x"])


class RecoveryDegradedCacheTests(TestCase):
    def test_degraded_cache_skips_recovery(self):
        import uuid
        from django.core.cache import cache
        from apps.deployments.tasks.build_recovery import (
            recover_corrupt_docker_state,
        )
        cache.clear()
        with patch.object(cache, "is_degraded", True, create=True), patch(
            "apps.deployments.tasks.build_recovery.perform_docker_recovery"
        ) as mock_perform:
            result = recover_corrupt_docker_state.run(
                deployment_id=str(uuid.uuid4()))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "cache_degraded")
        self.assertFalse(mock_perform.called)
