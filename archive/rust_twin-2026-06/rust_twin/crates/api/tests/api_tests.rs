use api::{routes, AppState};
use axum::{routing::get, Router};
use axum_test::TestServer;
use cn_core::config::Config;
use sea_orm::{Database, DbErr};
use std::sync::Arc;

/// A helper to spin up an in-memory SQLite database for fast unit testing,
/// bypassing the need for a running PostgreSQL instance in CI.
async fn setup_test_state() -> Result<Arc<AppState>, DbErr> {
    let db = Database::connect("sqlite::memory:").await?;

    let config = Config {
        secret_key: "test_secret_key_12345".to_string(),
        field_encryption_key: "test_encryption_key_12345".to_string(),
        database_url: "sqlite::memory:".to_string(),
        redis_host: "localhost".to_string(),
        redis_port: 6379,
        redis_password: None,
        debug: true,
        domain: "localhost".to_string(),
        use_ssl: false,
        host: "127.0.0.1".to_string(),
        port: 8000,
    };

    let redis_client = redis::Client::open("redis://127.0.0.1/").unwrap();

    Ok(Arc::new(AppState::new(db, config, redis_client)))
}

async fn health_check() -> &'static str {
    "OK"
}

#[tokio::test]
async fn test_health_check_endpoint() {
    let state = setup_test_state().await.expect("Failed to setup test state");
    let app = Router::new()
        .route("/health", get(health_check))
        .merge(routes::create_router(state));
    let server = TestServer::new(app).unwrap();
    let response = server.get("/health").await;
    response.assert_status_ok();
    response.assert_text("OK");
}

#[tokio::test]
async fn test_unauthorized_access_to_protected_route() {
    let state = setup_test_state().await.expect("Failed to setup test state");
    let app = Router::new()
        .route("/health", get(health_check))
        .merge(routes::create_router(state));
    let server = TestServer::new(app).unwrap();
    let response = server.get("/api/v1/projects").await;
    response.assert_status(axum::http::StatusCode::UNAUTHORIZED);
}
