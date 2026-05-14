import paramiko
import sys
import time

def monitor_updates():
    # Master
    m_host = "209.159.152.123"
    # Node
    n_host = "69.164.244.51"
    user = "root"
    password = "agbonsalo"
    
    hosts = [m_host, n_host]
    
    for host in hosts:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(hostname=host, username=user, password=password)
            print(f"--- Checking {host} Status ---")
            
            # Check if install lock exists
            stdin, stdout, stderr = client.exec_command("ls /tmp/smsly-install.lock 2>/dev/null")
            if stdout.read().decode().strip():
                print(f"Installer is still running on {host}.")
            else:
                # Check the last 5 lines of the install log
                stdin, stdout, stderr = client.exec_command("tail -n 5 /var/log/smsly-install.log")
                print(f"Latest logs from {host}:")
                print(stdout.read().decode().strip())
            
            client.close()
        except Exception as e:
            print(f"Error checking {host}: {e}")

if __name__ == "__main__":
    monitor_updates()
