FROM alpine:3.20

# One-shot host-network firewall shim used by the SMSLY backend.
#
# The backend container is unprivileged and has no iptables binary, so
# network_scope.py runs every firewall command through:
#   docker run --rm --net=host --cap-add=NET_ADMIN smsly/iptables-shim ...
#
# Stock alpine ships NEITHER iptables NOR nft — before this image existed
# every invocation died with "sh: iptables: not found" and every scoped
# project bridge shipped with no host-level egress isolation at all.
#
# This image carries both tools so the shim works on hosts regardless of
# whether their Docker firewall is backed by iptables-legacy or nftables.

RUN apk add --no-cache iptables nftables

# Idempotent listing entrypoint used by reconciles to inspect DOCKER-USER.
CMD ["iptables", "-S", "DOCKER-USER"]
