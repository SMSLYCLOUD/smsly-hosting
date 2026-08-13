# Trulay Grid CLI

The Trulay Grid command-line interface is used to operate a Grid installation from the terminal.

The package and executable remain `smsly` for compatibility. Renaming the command would break existing scripts, CI pipelines, and local configuration.

## Installation

Install using pip:
```bash
pip install smsly-cli
```

## Authentication

Create an API token in the Settings UI and authenticate:
```bash
smsly login --token "smsly_your_token_here"
```

## Commands

### Deployments
Deploy the current directory to your cloud platform:
```bash
smsly deploy
```

Watch deployment logs in real-time:
```bash
smsly logs <service_id>
```

Rollback to a previous deployment:
```bash
smsly rollback <service_id> <commit_hash>
```

### Configuration
Set environment variables:
```bash
smsly env set DATABASE_URL="postgres://..."
```

List environment variables:
```bash
smsly env ls
```
