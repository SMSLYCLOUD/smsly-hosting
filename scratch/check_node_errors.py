import paramiko
import sys

def check_node_errors():
    host = "69.164.244.51"
    user = "root"
    password = "agbonsalo"
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(hostname=host, username=user, password=password)
        # Search for real installer errors, ignoring socket-proxy spam
        cmd = "grep -v 'socket-proxy' /var/log/smsly-install.log | grep -iE 'error|failed|exit' | tail -n 20"
        stdin, stdout, stderr = client.exec_command(cmd)
        output = stdout.read()
        sys.stdout.buffer.write(output)
        client.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_node_errors()
