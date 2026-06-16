# CLI unification decision

## Current state

The repo ships three competing CLI implementations:

| File | Lang | LoC | Commands | Auth | Distribution |
|---|---|---|---|---|---|
| `cli/bin/smsly.js` | Node 20+ | 438 | login, services, ecosystem, tokens, logs, env, deploy | conf (keytar) | npm (not published) |
| `cli/smsly_cli.py` | Python 3.11+ | 428 | login, init, link, up, services, env, logs | /opt/smsly-hosting/.token file | pip (not published) |
| `cli/smsly.py` | Python (click) | 107 | login, deploy | none | none (DEAD) |

## Why three?

Historical: the Node CLI was written first, the Python argparse version was added for parity with the backend's Python stack, the Click version was a quick experiment. None were published to a registry; users run from the repo path.

## Decision: **deprecate the Python CLIs, keep the Node CLI as the single source of truth**

### Rationale

- **Most complete feature set**: Node CLI has 7 commands, argparse has 7, click has 2.
- **No external deps beyond stdlib + 4 well-maintained packages** (commander, chalk, ora, conf).
- **Independent of the backend's Python version** — operators can run the CLI from any machine with Node, without needing the backend's Python toolchain.
- **Already in the install path**: `install.sh` wires `cli/bin/smsly.js` to `/usr/local/bin/smsly`.

### Migration steps (out of scope for this PR)

1. Add `bin: { smsly: "bin/smsly.js" }` to `cli/package.json` and publish to npm.
2. Update `install.sh` and `backend/install.sh` to `npm install -g smsly-cli` (or `pnpm add -g`).
3. Remove `cli/smsly_cli.py`, `cli/smsly.py`, `cli/setup.py` from the repo (or move to `archive/dead-cli-2026-06/`).
4. Remove the `smsly-cli` console-script entry from `setup.py`.
5. Update `docs/CLI_USAGE.md` to reference the npm install.

## Alternatives considered

- **Keep argparse Python CLI as primary**: rejected because it's tied to Python and operators may not have the right version.
- **Keep all three**: rejected because it forces users to choose without a clear signal, and we can't reasonably maintain three.
- **Rewrite in Rust**: out of scope; the codebase already has 4 languages, adding a 5th is not justified for a CLI.

## Risks of the unification

- **Breaking change**: users with `pip install ./cli` workflows need to switch to `npm install`.
- **Node dependency**: operators without Node 20+ can't use the CLI. Mitigated by the fact that Node is already required for the frontend.
- **Lock-in to a single maintainer**: if the author of `smsly.js` leaves, the project loses CLI knowledge. Mitigate with good docs and onboarding.

## What this PR does NOT do

- Does not delete any CLI file.
- Does not change install behaviour.
- Does not publish to npm.

This PR only documents the decision. Implementation is a separate effort.
