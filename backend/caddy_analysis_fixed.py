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
    with open(caddyfile_path, 'r') as f:
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

    # Find domain blocks
    domain_blocks: list = []
    current_block: list = []
    current_domain = ""

    for i, line in enumerate(lines):
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('{') and line.endswith('{'):
            # Start of a new block
            if current_block:
                domain_blocks.append({
                    'domain': current_domain,
                    'lines': current_block.copy(),
                    'start_line': i - len(current_block)
                })
            current_domain = line[:-1].strip()  # Remove the {
            current_block = [line]
        elif current_block and line == '}':
            current_block.append(line)
            domain_blocks.append({
                'domain': current_domain,
                'lines': current_block.copy(),
                'start_line': i - len(current_block)
            })
            current_block = []
            current_domain = ""
        elif current_block:
            current_block.append(line)

    # Add any remaining block
    if current_block:
        domain_blocks.append({
            'domain': current_domain,
            'lines': current_block.copy(),
            'start_line': len(lines) - len(current_block)
        })

    print(f"Found {len(domain_blocks)} blocks:")
    for i, block in enumerate(domain_blocks):
        print(f"  Block {i+1}: {block['domain']}")

        # Check if it's a domain block
        if block['domain'] and not block['domain'].startswith('on_demand_tls') and not block['domain'].startswith(':'):
            # Look for TLS configuration
            tls_configured = False
            for line in block['lines']:
                if 'tls' in line.lower():
                    tls_configured = True
                    break

            # Look for on_demand_tls
            on_demand_tls = False
            for line in block['lines']:
                if 'on_demand' in line.lower():
                    on_demand_tls = True
                    break

            print(f"    TLS configured: {tls_configured}")
            print(f"    On-demand TLS: {on_demand_tls}")

            # Check if domain is our test domain
            if 'working-test.example.com' in block['domain']:
                print("    -> This is our test domain block!")
                print(f"    -> TLS: {tls_configured}, On-demand: {on_demand_tls}")

    print()

    # Check for on_demand_tls global configuration
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

    # Check if domains are properly included
    print("4. Domain Inclusion Check")
    print("-" * 40)

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

    # Generate expected Caddyfile for comparison
    print("5. Expected Caddyfile Generation")
    print("-" * 40)

    try:
        from apps.deployments.models import PlatformConfig
        from services.caddy_manager import generate_caddyfile

        config = PlatformConfig.load()
        expected_caddyfile = generate_caddyfile(config)

        print("Expected Caddyfile (first 20 lines):")
        expected_lines = expected_caddyfile.split('\n')[:20]
        for i, line in enumerate(expected_lines):
            print(f"  {i+1:2d}: {line}")

        print(f"\nTotal expected lines: {len(expected_caddyfile.split('\n'))}")

        # Check if test domain is in expected Caddyfile
        if test_domain in expected_caddyfile:
            print("Test domain should be in generated Caddyfile")
        else:
            print("Test domain should NOT be in generated Caddyfile")

    except Exception as e:
        print(f"Error generating expected Caddyfile: {e}")

    print()

except Exception as e:
    print(f"Error: {e}")

print("\n=== End Caddy Analysis ===")
