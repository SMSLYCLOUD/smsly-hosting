import paramiko
import sys

def update_node():
    host = "69.164.244.51"
    user = "root"
    password = "agbonsalo"
    db_pass = "XuWZcZ-HURhYB1ZngdgAK0tq2fBT_AAn"
    master_ip = "209.159.152.123"
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(hostname=host, username=user, password=password)
        print(f"Connected to Node {host}. Updating with Permanent Seed...")
        
        # Pull and run update on Node with Master details to create the seed
        cmd = f"cd /opt/smsly-hosting && git reset --hard HEAD && git pull origin main && sudo MASTER_IP='{master_ip}' MASTER_DB_PASSWORD='{db_pass}' bash install.sh --update"
        stdin, stdout, stderr = client.exec_command(cmd)
        
        for line in stdout:
            l = line.strip()
            if any(k in l.lower() for k in ["rebuilding", "starting", "complete", "checkpoint", "seed"]):
                sys.stdout.buffer.write(f"[node] {l}\n".encode('utf-8'))
        
        print("Node update command finished.")
        client.close()
    except Exception as e:
        print(f"Error on node: {e}")

if __name__ == "__main__":
    update_node()
