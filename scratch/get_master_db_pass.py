import paramiko
import sys

def get_master_env():
    host = "209.159.152.123"
    user = "root"
    password = "agbonsalo"
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(hostname=host, username=user, password=password)
        # Check if UFW is active on Master
        stdin, stdout, stderr = client.exec_command("sudo ufw status")
        output = stdout.read().decode().strip()
        print(f"Master Firewall Status:\n{output}")
        
        # SSH into the node and run the final update
        node_ip = "69.164.244.51"
        node_pass = "agbonsalo"
        db_pass = "Q5Av2lV_8UbzEftPMRk_2lj-XDBgRQJ3"
        master_ip = "209.159.152.123"
        
        node_client = paramiko.SSHClient()
        node_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            node_client.connect(hostname=node_ip, username="root", password=node_pass)
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
            
            node_client.close()
        except Exception as e:
            print(f"Error on node: {e}")
        client.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_master_env()
