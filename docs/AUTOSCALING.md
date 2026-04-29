# Auto-Scaling

The autoscaler monitors CPU utilization metrics and scales services vertically or horizontally.

## Scaling Boundaries and Hysteresis
- **Scale Up:** Restricted by `max_replicas`. Subject to a global 1-minute cooldown to prevent flapping.
- **Scale Down:** Restricted by an absolute floor of `1`. Subject to a strict 5-minute cooldown constraint.
- The autoscaler strictly enforces state using the service's `min_replicas` attribute.
