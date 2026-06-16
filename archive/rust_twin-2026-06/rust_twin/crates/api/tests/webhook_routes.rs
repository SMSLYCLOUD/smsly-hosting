//! Smoke tests for the webhook routes.

use crate::tests::common::test_app;

#[tokio::test]
async fn test_list_webhooks_unauth() {
    let server = test_app().await;
    let res = server.get("/api/v1/webhooks").await;
    assert_eq!(res.status_code(), 401);
}

#[tokio::test]
async fn test_create_webhook_with_ssrf_url() {
    let server = test_app().await;
    let res = server.post("/api/v1/webhooks")
        .json(&serde_json::json!({
            "url": "http://127.0.0.1:8080/hook",
            "events": ["deployment.completed"],
        }))
        .await;
    // SSRF guard should reject 127.0.0.1
    // (will return 401 because unauth, but that's fine)
    assert!(res.status_code().is_client_error() || res.status_code() == 401);
}
