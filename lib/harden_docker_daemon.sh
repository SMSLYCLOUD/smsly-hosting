#!/bin/bash

_harden_docker_daemon_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    [ ! -f "$daemon_cfg" ] && echo '{}' > "$daemon_cfg"

    local changed=false

    # log rotation
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('log-driver')=='json-file' and d.get('log-opts',{}).get('max-size')=='10m' else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['log-driver'] = 'json-file'
cfg['log-opts'] = {'max-size': '10m', 'max-file': '3'}
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # live-restore
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('live-restore') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['live-restore'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # seccomp
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('features',{}).get('seccomp') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg.setdefault('features', {})['seccomp'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # Restart Docker if config changed AND no SMSLY containers are running
    # (doing so live would kill production).
    if [ "$changed" = "true" ]; then
        local _smsly_ctrs
        _smsly_ctrs="$(docker ps --format '{{.Names}}'  | grep -c smsly || true)"
        if [ "$_smsly_ctrs" -eq 0 ]; then
            _harden_log info "Docker daemon config changed — restarting Docker..."
            systemctl restart docker || { _harden_log error "Docker restart failed"; }
            for _i in $(seq 1 30); do
                docker info  && break
                sleep 2
            done
            _harden_log ok "Docker daemon restarted with security config"
        else
            _harden_log warn "Docker daemon config changed but $_smsly_ctrs SMSLY containers are running — deferring restart (apply on next daemon reload)"
        fi
    fi
}

_harden_docker_daemon_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    if [ -f "$daemon_cfg" ] && python3 -c "import json; json.load(open('$daemon_cfg'))" ; then
        _harden_log ok "docker daemon security config present"
        return 0
    fi
    _harden_log warn "docker daemon config missing or invalid"
    return 1
}
