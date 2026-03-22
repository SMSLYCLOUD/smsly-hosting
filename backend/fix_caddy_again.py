import subprocess

caddy_conf = """
{
    admin off
}

:4000 {
    reverse_proxy 172.21.0.1:11434 {
        header_up Host {upstream_hostport}
    }
    
    handle_path /health {
        respond 200 {
            body "OK"
            close
        }
    }
}
"""

with open("/tmp/Caddyfile", "w") as f:
    f.write(caddy_conf)

subprocess.run(["docker", "cp", "/tmp/Caddyfile", "llama-proxy-fdd07af0:/etc/caddy/Caddyfile"])
subprocess.run(["docker", "restart", "llama-proxy-fdd07af0"])
print("Proxy updated.")
