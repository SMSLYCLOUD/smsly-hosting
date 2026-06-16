use api::{routes, AppState};
use axum::{routing::get, Router};
use axum_test::TestServer;
use cn_core::config::Config;
use sea_orm::{Database, DbErr};
use std::sync::Arc;

/// A helper to spin up an in-memory SQLite database for fast unit testing,
/// bypassing the need for a running PostgreSQL instance in CI.
async fn setup_test_state() -> Result<Arc<AppState>, DbErr> {
    // 1. Connect to in-memory SQLite
    let db = Database::connect("sqlite::memory:").await?;

    // Note: In a full integration test, we would run SeaORM migrations here
    // e.g., `Migrator::up(&db, None).await?;`

    // 2. Mock Configuration
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

    // 3. Connect to a mock/dummy Redis (or just let it fail gracefully in tests that don't need it)
    // For this basic health test, we don't strictly need a real Redis connection if we aren't testing queues.
    let redis_client = redis::Client::open("redis://127.0.0.1/").unwrap();

    Ok(Arc::new(AppState {
        db,
        config,
        redis: redis_client,
    }))
}

// Replicate the basic health check from main.rs
async fn health_check() -> &'static str {
    "OK"
}

fn build_test_app(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/health", get(health_check))
        .merge(routes::create_router())
        .with_state(state)
}

#[tokio::test]
async fn test_health_check_endpoint() {
    // 1. Setup State
    let state = setup_test_state().await.expect("Failed to setup test state");

    // 2. Build Router
    let app = build_test_app(state);

    // 3. Initialize Test Server
    let server = TestServer::new(app).unwrap();

    // 4. Send GET request to /health
    let response = server.get("/health").await;

    // 5. Assertions
    response.assert_status_ok();
    response.assert_text("OK");
}

#[tokio::test]
async fn test_unauthorized_access_to_protected_route() {
    let state = setup_test_state().await.expect("Failed to setup test state");
    let app = build_test_app(state);
    let server = TestServer::new(app).unwrap();

    // The /api/v1/projects endpoint requires a valid JWT Bearer token
    let response = server.get("/api/v1/projects").await;

    // We expect a 401 Unauthorized because we didn't attach an Authorization header
    response.assert_status(axum::http::StatusCode::UNAUTHORIZED);
}