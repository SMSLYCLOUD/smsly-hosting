use axum::{routing::{get, post}, Router};
use std::sync::Arc;

use crate::{AppState, handlers::{project, auth, billing}};

pub fn create_router() -> Router<Arc<AppState>> {
    Router::new()
        .nest(
            "/api/v1",
            Router::new()
                // Public Routes
                .route("/auth/register", post(auth::register))
                .route("/auth/login", post(auth::login))
                // Protected Routes (Uses `AuthUser` extractor in handlers)
                .route("/projects", get(project::list_projects).post(project::create_project))
                .route("/projects/:id/deploy", post(project::trigger_deploy))
                // Billing / Licensing
                .route("/billing/license", get(billing::get_license))
                .route("/billing/upgrade", post(billing::upgrade_license))
        )
}
