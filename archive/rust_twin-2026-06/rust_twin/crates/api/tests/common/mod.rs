//! Test helpers for the api crate's integration tests.

use std::sync::Arc;

use api::AppState;
use axum_test::TestServer;
use sea_orm::{DatabaseBackend, MockDatabase};

pub fn mock_state() -> Arc<AppState> {
    let db = MockDatabase::new(DatabaseBackend::Postgres).into_connection();
    let config = cn_core::config::Config {
        database_url: "postgres://mock".into(),
        redis_url: "redis://mock".into(),
        secret_key: "test_secret".into(),
        host: "127.0.0.1".into(),
        port: 8080,
    };
    let redis_client = redis::Client::open("redis://mock").unwrap();
    Arc::new(AppState::new(db, config, redis_client))
}

pub async fn test_app() -> TestServer {
    let state = mock_state();
    let app = crate::routes::create_router(state);
    TestServer::new(app).unwrap()
}
