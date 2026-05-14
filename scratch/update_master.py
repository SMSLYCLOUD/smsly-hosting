import paramiko
import sys

def update_master():
    host = "209.159.152.123"
    user = "root"
    password = "agbonsalo"
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(hostname=host, username=user, password=password)
        print(f"Connected to Master {host}. Updating...")
        
        # Pull and run update on Master
        cmd = "cd /opt/smsly-hosting && git reset --hard HEAD && git pull origin main && sudo bash install.sh --update"
        stdin, stdout, stderr = client.exec_command(cmd)
        
        # We won't stream everything as it might be too long, but we'll print key progress
        for line in stdout:
            l = line.strip()
            if any(k in l.lower() for k in ["rebuilding", "starting", "complete", "checkpoint"]):
                sys.stdout.buffer.write(f"[master] {l}\n".encode('utf-8'))
        
        print("Master update command finished.")
        client.close()
    except Exception as e:
        print(f"Error on master: {e}")

if __name__ == "__main__":
    update_master()
