import paramiko
USER='root'; PW='agbonsalo'
PRIMARY='163.245.216.249'
REPLICA='163.245.216.248'
priv_primary='mKrcJ8k/EcUEDWtNbgm4tsqlUhbZZ9wkAobJbKIGC3Q='
priv_replica='CPr1K0qj3QMEeZUd7Gt9WfH1UJaysLNSXHeXZ+25T2w='
pub_primary='Ku5um9y1f8ue/tjhQOIUbUlKylPRdLRdezds7DSYjHI='
pub_replica='acvMT9bB9GiIn0zwCCgWnPQZv61/UWnv0ZUQls7dVgg='
confs={
    PRIMARY: f"[Interface]\nAddress = 10.10.0.1/24\nListenPort = 51820\nPrivateKey = {priv_primary}\n\n[Peer]\nPublicKey = {pub_replica}\nAllowedIPs = 10.10.0.2/32\nEndpoint = {REPLICA}:51820\nPersistentKeepalive = 15\n",
    REPLICA: f"[Interface]\nAddress = 10.10.0.2/24\nListenPort = 51820\nPrivateKey = {priv_replica}\n\n[Peer]\nPublicKey = {pub_primary}\nAllowedIPs = 10.10.0.1/32\nEndpoint = {PRIMARY}:51820\nPersistentKeepalive = 15\n",
}
for host,conf in confs.items():
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(host, username=USER, password=PW, timeout=10, banner_timeout=10, auth_timeout=10)
    sftp=c.open_sftp();
    with sftp.file('/etc/wireguard/wg0.conf','w') as f:
        f.write(conf)
    sftp.chmod('/etc/wireguard/wg0.conf',0o600)
    sftp.close(); c.close();
    print(f"{host} wg0.conf updated")
