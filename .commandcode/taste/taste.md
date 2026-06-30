# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# architecture
- New installer functionality goes into separate files under lib/ and is sourced/linked from install.sh, not added directly to install.sh. Confidence: 0.75
- Kubernetes-related code (Helm charts, PodSecurity helpers, k8s templates) is no longer used — skip all k8s fixes and do not spend time on k8s-related work. Confidence: 0.70
- Do not modify SSH authentication settings (PasswordAuthentication, PermitRootLogin) in harden.sh — the platform manages SSH auth via scope key after provisioning. Confidence: 0.70

