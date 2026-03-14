use axum::{routing::{get, post}, Router};
use std::sync::Arc;

use crate::{AppState, handlers::project};

pub fn create_router() -> Router<Arc<AppState>> {
    Router::new()
        .nest(
            "/api/v1",
            Router::new()
                .route("/projects", get(project::list_projects).post(project::create_project))
                // Add more domain routes here as Phase 3 expands
        )
}
