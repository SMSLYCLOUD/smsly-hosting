//! Smoke tests for the service routes.

use crate::tests::common::test_app;

#[tokio::test]
async fn test_list_services_unauth() {
    let server = test_app().await;
    let res = server.get("/api/v1/services").await;
    assert_eq!(res.status_code(), 401);
}
