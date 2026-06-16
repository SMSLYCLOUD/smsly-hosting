# DEAD CODE QUARANTINE (2026-06 cleanup)

<!-- DEAD CODE QUARANTINE -->
<!-- This file is the manifest for archive/. The all-caps marker is required so
     scripts/check_dead_code_quarantine.sh and the user verification command
     below can confirm the manifest has not been deleted. -->

The following directories/files were moved into `archive/` after a deep sweep
found they were not referenced by any production code, build, deploy, or
install path in the SMSLY hosting repository.

| Path | Original location | Reason for quarantine |
|---|---|---|
| `custom-addons-2026-06/` | `custom-addons/` | Misnamed directory. Its README claimed "Odoo addons" but it contained a 135-line Rust TCP broker skeleton. Not referenced by `docker-compose.yml`, `docker-compose.prod.yml`, the Helm chart, the backend Python code, or `install.sh`. The README claim was false. |
| `rust_twin-2026-06/` | `rust_twin/` | Cargo workspace with 7 crates (`core`, `api`, `worker`, `cli`, `infrastructure`, `frontend`, `intelligence`). The accompanying `PARITY_REPORT.md` shows fake identical 10.00 ms latencies across all endpoints, which is not a credible parity test. Most `.rs` files are scaffolding only. The `RUST_TWIN_MODE` branch in `install.sh` and the `rust-ci.yml` workflow will no longer function and must be retired in a follow-up. |
| `console-2026-06/` | `console/` | Python FastAPI/Starlette service with its own `INSTALL.md`. Not registered in `INSTALLED_APPS`, not referenced by any compose file, and its install steps are not wired into `install.sh`. |
| `dead-cli-2026-06/smsly.py` | `cli/smsly.py` | Click-based CLI superseded by `cli/smsly_cli.py` (argparse). `cli/setup.py` and the `cli/README.md` only document `smsly_cli.py`. The Click version is unused. |

## How to restore (do not do this lightly)

If you need to revive any of these:

1. `mv archive/<name>-2026-06/ ./<original_path>/`
2. Re-integrate the imports, `INSTALLED_APPS` entries, compose services,
   `pytest.ini` testpaths, and installer hooks that reference the stub.
3. Add tests before shipping. The "rust_twin" stub in particular has a
   `PARITY_REPORT.md` with fabricated identical latencies — replace it with
   a real, end-to-end test suite before claiming parity.

## Follow-ups (all retired 2026-06)

- [x] `install.sh` RUST_TWIN_MODE branch — removed in Batch S12
- [x] `pytest.ini:6` `rust_twin` in testpaths — removed in Batch S12
- [x] `.github/workflows/rust-ci.yml` — deleted in Batch S12

## Verification

Run `scripts/check_dead_code_quarantine.sh` to confirm:

- no production code imports from `archive/`
- the original stub directories (`custom-addons/`, `rust_twin/`, `console/`)
  have not been silently restored without re-integration
- the legacy CLI at `cli/smsly.py` has not been restored

See also the `.gitignore` block for `archive/**/target/`,
`archive/**/node_modules/`, and `archive/**/__pycache__/` — the archive
directories are tracked, but their build outputs are not.
