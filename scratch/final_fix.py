import paramiko
import sys

def apply_final_fix():
    node_ip = "69.164.244.51"
    node_pass = "agbonsalo"
    db_pass = "XuWZcZ-HURhYB1ZngdgAK0tq2fBT_AAn"
    master_ip = "209.159.152.123"
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=node_ip, username="root", password=node_pass)
        print(f"Applying final hardening to {node_ip}...")
        
        # This will create the .agent_lite_seed file
        cmd = f"cd /opt/smsly-hosting && git reset --hard HEAD && git pull origin main && MASTER_IP='{master_ip}' MASTER_DB_PASSWORD='{db_pass}' bash install.sh --update"
        stdin, stdout, stderr = client.exec_command(cmd)
        
        for line in stdout:
            sys.stdout.buffer.write(f"[node] {line.strip()}\n".encode('utf-8'))
        for line in stderr:
            sys.stdout.buffer.write(f"[node-err] {line.strip()}\n".encode('utf-8'))
            
        print("Final fix applied.")
        client.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    apply_final_fix()
