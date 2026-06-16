//! Smoke tests for the billing routes.

use crate::tests::common::test_app;

#[tokio::test]
async fn test_list_plans_unauth() {
    let server = test_app().await;
    let res = server.get("/api/v1/billing/plans").await;
    assert_eq!(res.status_code(), 401);
}
