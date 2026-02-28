import paramiko
import time

caddyfile = """cloud.smsly.cloud {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

*.cloud.smsly.cloud {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    reverse_proxy localhost:8081
}

:80 {
    reverse_proxy localhost:8090
}
"""

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

connected = False
for _ in range(5):
    try:
        print("Connecting...")
        c.connect("163.245.216.248", username="root", password="agbonsalo", timeout=10)
        connected = True
        break
    except Exception as e:
        print("Retrying:", type(e).__name__)
        time.sleep(5)

if connected:
    print("Connected. Writing Caddyfile...")
    sftp = c.open_sftp()
    
    with open("caddyfile_tmp2", "w") as f:
        f.write(caddyfile)
        
    sftp.put("caddyfile_tmp2", "/tmp/Caddyfile")
    sftp.close()

    c.exec_command("mv /tmp/Caddyfile /etc/caddy/Caddyfile")

    print("\\nReloading Caddy...")
    _, stdout, stderr = c.exec_command("systemctl reload caddy")
    print(stdout.read().decode("utf-8", errors="replace"))
    
    print("\\nChecking status...")
    _, stdout, stderr = c.exec_command("curl -kI https://cloud.smsly.cloud")
    print(stdout.read().decode("utf-8", errors="replace"))
else:
    print("Failed to connect after 5 attempts.")
