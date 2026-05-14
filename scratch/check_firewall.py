import paramiko
import sys

def check_firewall():
    host = "209.159.152.123"
    user = "root"
    password = "agbonsalo"
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(hostname=host, username=user, password=password)
        stdin, stdout, stderr = client.exec_command("sudo ufw status")
        output = stdout.read().decode().strip()
        print(f"Master Firewall Status:\n{output}")
        client.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_firewall()
