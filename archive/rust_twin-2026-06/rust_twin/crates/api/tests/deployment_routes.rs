//! Smoke tests for the deployment routes.

use crate::tests::common::test_app;
use serde_json::json;

#[tokio::test]
async fn test_list_deployments_unauth() {
    let server = test_app().await;
    let res = server.get("/api/v1/deployments").await;
    // Without a valid AuthUser, should return 401
    assert_eq!(res.status_code(), 401);
}

#[tokio::test]
async fn test_trigger_deployment_validation() {
    let server = test_app().await;
    let res = server.post("/api/v1/deployments").json(&json!({})).await;
    // Missing required fields should return 400 or 422
    assert!(res.status_code().is_client_error());
}
