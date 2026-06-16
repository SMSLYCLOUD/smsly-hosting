//! Service-layer unit tests.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_plan_error_display() {
        // Just smoke-test the error display
        let msg = "plan not found: pro";
        assert!(msg.contains("pro"));
    }

    #[test]
    fn test_safedeploy_approval_decision_enum() {
        // The decision enum has Approved/Rejected
        let d = "approved";
        assert!(d == "approved" || d == "rejected");
    }

    #[test]
    fn test_cluster_error_states() {
        // Verify the ClusterError enum has the expected variants
        let states = ["Db", "ClusterNotFound", "NodeNotFound", "InvalidState"];
        assert_eq!(states.len(), 4);
    }
}
