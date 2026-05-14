import paramiko
import sys

def check_node_state():
    # Master
    host = "209.159.152.123"
    user = "root"
    password = "agbonsalo"
    
    # Node
    node_ip = "69.164.244.51"
    node_pass = "agbonsalo"

    node_client = paramiko.SSHClient()
    node_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        node_client.connect(hostname=node_ip, username="root", password=node_pass)
        print(f"Connected to Node {node_ip}. Verifying state...")
        
        # Check the .env file on the node
        stdin, stdout, stderr = node_client.exec_command("cat /opt/smsly-hosting/.env | grep -E 'MASTER_IP|DATABASE_URL'")
        print("--- Node Env Check ---")
        for line in stdout:
            print(line.strip())
        
        # Check if seed file exists
        stdin, stdout, stderr = node_client.exec_command("ls -la /opt/smsly-hosting/.agent_lite_seed")
        print("--- Seed File Check ---")
        for line in stdout:
            print(line.strip())
            
        # Check docker containers
        stdin, stdout, stderr = node_client.exec_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
        print("--- Container Check ---")
        for line in stdout:
            print(line.strip())
        
        node_client.close()
    except Exception as e:
        print(f"Error on node: {e}")

if __name__ == "__main__":
    check_node_state()
