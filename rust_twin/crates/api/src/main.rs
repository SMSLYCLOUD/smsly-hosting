use axum::{routing::get, Router};
use cn_core::config::Config;
use cn_core::db;
use cn_core::telemetry;
use tokio::net::TcpListener;
use tracing::info;
use anyhow::{Context, Result};
use std::sync::Arc;
use sea_orm::DatabaseConnection;

pub mod handlers;
pub mod routes;

pub struct AppState {
    pub db: DatabaseConnection,
    pub config: Config,
}

#[tokio::main]
async fn main() -> Result<()> {
    // 1. Initialize telemetry
    telemetry::init()?;

    // 2. Load config
    info!("Loading configuration...");
    let config = Config::load().context("Failed to load environment configuration")?;

    // 3. Connect to database
    info!("Connecting to database at {}", &config.database_url);
    let db = db::establish_connection(&config.database_url).await?;

    // 4. Set up App State
    let state = Arc::new(AppState { db, config: config.clone() });

    // 5. Build Axum Router
    let app = Router::new()
        .route("/health", get(health_check))
        // Mount v1 API routes
        .merge(routes::create_router())
        .with_state(state);

    // 6. Bind and Serve
    let addr = format!("{}:{}", config.host, config.port);
    info!("Starting Axum server on {}", addr);
    let listener = TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

async fn health_check() -> &'static str {
    "OK"
}
