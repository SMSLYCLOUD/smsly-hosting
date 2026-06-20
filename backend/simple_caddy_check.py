#!/usr/bin/env python
import os
import sys

import django

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Setup Django
django.setup()

print("=== Caddy Configuration Analysis ===\n")

# Read the actual Caddyfile
caddyfile_path = "C:\\Users\\osaretin\\Documents\\SMSLY\\SMSLY_CORE\\smsly-hosting\\caddy-config\\Caddyfile"

try:
    with open(caddyfile_path) as f:
        caddyfile_content = f.read()

    print("1. Current Caddyfile Content")
    print("-" * 40)
    print(caddyfile_content)
    print()

    # Analyze Caddyfile structure
    print("2. Caddyfile Analysis")
    print("-" * 40)

    lines = caddyfile_content.split('\n')
    print(f"Total lines: {len(lines)}")

    # Check if our test domain is in the Caddyfile
    test_domain = 'working-test.example.com'
    domain_in_caddyfile = test_domain in caddyfile_content

    print(f"Test domain '{test_domain}' in Caddyfile: {domain_in_caddyfile}")

    if domain_in_caddyfile:
        print("Domain is included in Caddyfile")

        # Check how it's configured
        lines_with_domain = [i for i, line in enumerate(lines) if test_domain in line]
        print(f"Domain appears on lines: {lines_with_domain}")

        for line_num in lines_with_domain:
            print(f"  Line {line_num}: {lines[line_num].strip()}")
    else:
        print("Domain is NOT included in Caddyfile")

    print()

    # Check for on_demand_tls configuration
    print("3. On-Demand TLS Configuration")
    print("-" * 40)

    on_demand_found = False
    ask_endpoint = None

    for line in lines:
        if 'on_demand_tls' in line:
            on_demand_found = True
            print(f"Found on_demand_tls: {line.strip()}")
        elif 'ask' in line and 'http' in line:
            ask_endpoint = line.strip()
            print(f"Found ask endpoint: {ask_endpoint}")

    if not on_demand_found:
        print("No on_demand_tls configuration found")

    if not ask_endpoint:
        print("No ask endpoint found")

    print()

    # Check Caddy reload status
    print("4. Caddy Reload Status")
    print("-" * 40)

    # Check if reload flag exists
    reload_flag_path = "C:\\Users\\osaretin\\Documents\\SMSLY\\SMSLY_CORE\\smsly-hosting\\caddy-config\\.reload"

    if os.path.exists(reload_flag_path):
        print("Reload flag exists - Caddy should have reloaded")
        with open(reload_flag_path) as f:
            reload_content = f.read()
        print(f"Reload flag content: {reload_content}")
    else:
        print("Reload flag does not exist - Caddy has not been reloaded")

    print()

except Exception as e:
    print(f"Error analyzing Caddyfile: {e}")

print("=== End Caddy Analysis ===")
