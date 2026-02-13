#!/usr/bin/env python3
"""
SMSLY CLI Tool
The developer-first interface for SMSLY Hosting.
"""
import os
import sys
import json
import zipfile
import requests
import click
from pathlib import Path

API_URL = os.environ.get("SMSLY_API_URL", "http://localhost:8000/api/v1")
CONFIG_FILE = Path.home() / ".smsly" / "config.json"

def load_config():
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def save_config(config):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f)

@click.group()
def cli():
    """SMSLY Hosting CLI"""
    pass

@cli.command()
@click.option('--email', prompt=True)
@click.option('--password', prompt=True, hide_input=True)
def login(email, password):
    """Login to SMSLY Hosting"""
    try:
        response = requests.post(f"{API_URL}/auth/login/", json={
            # Support both username/email logins (depends on server auth config).
            "username": email,
            "email": email,
            "password": password
        })
        if response.status_code == 200:
            token = response.json().get('key')
            save_config({"token": token, "email": email})
            click.echo(click.style("Login successful!", fg="green"))
        else:
            click.echo(click.style(f"Login failed: {response.text}", fg="red"))
    except Exception as e:
        click.echo(click.style(f"Error: {e}", fg="red"))

@cli.command()
@click.argument('path', default='.', type=click.Path(exists=True))
@click.option('--service', prompt='Service ID', help='UUID of the service to deploy to')
def deploy(path, service):
    """Deploy the current directory"""
    config = load_config()
    token = config.get('token')
    if not token:
        click.echo("Please login first: smsly login")
        return

    # Zip the directory
    zip_path = Path("deploy_artifact.zip")
    click.echo(f"Zipping {path}...")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(path):
            # Ignore git, venv, node_modules
            dirs[:] = [d for d in dirs if d not in ['.git', 'venv', 'node_modules', '__pycache__']]
            for file in files:
                if file == "deploy_artifact.zip": continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, path)
                zipf.write(file_path, arcname)

    # Upload
    click.echo("Uploading...")
    headers = {'Authorization': f'Token {token}'}
    with open(zip_path, 'rb') as f:
        files = {'file': ('source.zip', f, 'application/zip')}
        data = {'service_id': service}

        try:
            response = requests.post(
                f"{API_URL}/deployments/upload/",
                headers=headers,
                files=files,
                data=data
            )
            if response.status_code == 201:
                data = response.json()
                click.echo(click.style(f"Deployment Triggered! ID: {data['deployment_id']}", fg="green"))
            else:
                click.echo(click.style(f"Upload failed: {response.text}", fg="red"))
        except Exception as e:
            click.echo(click.style(f"Network error: {e}", fg="red"))
        finally:
            # Cleanup
            if zip_path.exists():
                os.remove(zip_path)

if __name__ == '__main__':
    cli()
