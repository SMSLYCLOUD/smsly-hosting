import paramiko
import sys

def fix_lite_mode():
    node_ip = "69.164.244.51"
    node_pass = "agbonsalo"
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=node_ip, username="root", password=node_pass)
        print(f"Connected to Node {node_ip}. Re-configuring as Lite Agent...")
        
        # Run the official hardened installer in lite mode
        db_pass = "XuWZcZ-HURhYB1ZngdgAK0tq2fBT_AAn"
        master_ip = "209.159.152.123"
        cmd = f"cd /opt/smsly-hosting && git pull origin main && MASTER_IP='{master_ip}' MASTER_DB_PASSWORD='{db_pass}' bash install.sh --update --lite"
        stdin, stdout, stderr = client.exec_command(cmd)
        
        for line in stdout:
            sys.stdout.buffer.write(f"[node] {line.strip()}\n".encode('utf-8'))
        for line in stderr:
            sys.stdout.buffer.write(f"[node-err] {line.strip()}\n".encode('utf-8'))
            
        print("Node re-configured successfully.")
        client.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fix_lite_mode()
