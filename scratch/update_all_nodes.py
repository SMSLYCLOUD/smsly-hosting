import subprocess
import sys

def run_ssh(host, command):
    print(f"--- Running on {host} ---")
    full_cmd = f'ssh -o StrictHostKeyChecking=no root@{host} "{command}"'
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(f"Error on {host}: {result.stderr}")
    return result.returncode

update_cmd = "cd /opt/smsly-hosting; git pull origin main; bash install.sh"
lite_update_cmd = "cd /opt/smsly-hosting; git pull origin main; bash install.sh --lite"

# Update Master
run_ssh("209.159.152.123", update_cmd)

# Update Lite Agent
run_ssh("69.164.244.51", lite_update_cmd)
