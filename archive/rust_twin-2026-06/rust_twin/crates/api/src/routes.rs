use axum::{
    extract::State,
    middleware::from_fn_with_state,
    routing::{delete, get, post},
    Router,
};
use std::sync::Arc;

use crate::middleware::hmac::hmac_middleware;
use crate::middleware::ratelimit::rate_limit_middleware;
use crate::{handlers::{acme, addon, admin, auth, backup, billing, deployment, domain, marketplace, observability, project, service, sso, team, transfer, tunnel, webhook}, AppState};

pub fn create_router(state: Arc<AppState>) -> Router {
    let internal = Router::new()
        .route("/api/v1/internal/deploy", post(internal_deploy))
        .route("/api/v1/internal/heartbeat", post(internal_heartbeat))
        .layer(from_fn_with_state(state.hmac.clone(), hmac_middleware));

    Router::new()
        .merge(public_routes())
        .merge(internal)
        .route("/health", get(observability::health))
        .route("/health/live", get(observability::health_live))
        .route("/health/ready", get(observability::health_ready))
        .route("/metrics", get(observability::metrics))
        .route("/version", get(observability::version))
        .route("/.well-known/acme-challenge/:token", get(acme::acme_challenge))
        .route("/acme/directory", get(acme::acme_directory))
        .layer(from_fn_with_state(
            state.rate_limit.clone(),
            rate_limit_middleware,
        ))
        .with_state(state)
}

pub fn public_routes() -> Router<Arc<AppState>> {
    Router::new()
        .nest(
            "/api/v1",
            Router::new()
                .route("/auth/register", post(auth::register))
                .route("/auth/login", post(auth::login))
                .route("/auth/logout", post(auth::logout))
                .route("/auth/refresh", post(auth::refresh_token))
                .route("/auth/me", get(auth::me))
                .route("/auth/password", post(auth::change_password))
                .route(
                    "/projects",
                    get(project::list_projects).post(project::create_project),
                )
                .route("/projects/:id", get(project::get_project))
                .route("/projects/:id/services", get(project::list_project_services))
                .route("/projects/:id/deploy", post(project::trigger_deploy))
                .route("/services", get(service::list_services).post(service::create_service))
                .route("/services/:id", get(service::get_service).delete(service::delete_service))
                .route("/services/:id/env-vars", get(service::list_env_vars))
                .route("/services/:id/deployments/latest", get(service::get_latest_deployment))
                .route("/billing/plans", get(billing::list_plans))
                .route(
                    "/billing/subscription",
                    get(billing::get_my_subscription).post(billing::upgrade_subscription),
                )
                .route("/billing/subscription/cancel", post(billing::cancel_subscription))
                .route("/billing/invoices", get(billing::list_my_invoices))
                .route("/billing/license", get(billing::get_license))
                .route("/billing/upgrade", post(billing::upgrade_license))
                .route(
                    "/teams",
                    get(team::list_my_teams).post(team::create_team),
                )
                .route(
                    "/teams/:id",
                    get(team::get_team).delete(team::delete_team),
                )
                .route(
                    "/teams/:id/members",
                    get(team::list_team_members).post(team::add_team_member),
                )
                .route(
                    "/teams/:id/members/:user_id",
                    delete(team::remove_team_member),
                )
                .route(
                    "/teams/:id/transfer-ownership/:user_id",
                    post(team::transfer_ownership),
                )
                .route(
                    "/services/:id/domains",
                    get(domain::list_domains).post(domain::create_domain),
                )
                .route("/domains/:id", delete(domain::delete_domain))
                .route("/domains/:id/verify", post(domain::verify_domain))
                .route("/services/:id/tunnels", get(tunnel::list_tunnels))
                .route("/tunnels/:id", get(tunnel::get_tunnel))
                .route("/tunnels/:id/disable", post(tunnel::disable_tunnel))
                .route("/transfers", get(transfer::list_transfers))
                .route("/transfers/:id", get(transfer::get_transfer))
                .route("/transfers/:id/cancel", post(transfer::cancel_transfer))
                .route(
                    "/webhooks",
                    get(webhook::list_webhooks).post(webhook::create_webhook),
                )
                .route("/webhooks/:id", delete(webhook::delete_webhook))
                .route("/webhooks/:id/test", post(webhook::test_webhook))
                .route("/sso/authorize", get(sso::oauth_authorize))
                .route("/sso/callback", get(sso::oauth_callback))
                .route("/sso/accounts", get(sso::list_social_accounts))
                .route(
                    "/services/:id/backups",
                    get(backup::list_backups).post(backup::create_backup),
                )
                .route(
                    "/backups/:id",
                    get(backup::get_backup).delete(backup::delete_backup),
                )
                .route("/backups/:id/verify", post(backup::verify_backup))
                .route("/backups/:id/restore", post(backup::restore_backup))
                .route("/backups/:id/download", get(backup::download_backup))
                .route("/backups/retention", post(backup::apply_retention))
                .route(
                    "/deployments",
                    get(deployment::list_deployments).post(deployment::trigger_deployment),
                )
                .route("/deployments/:id", get(deployment::get_deployment))
                .route("/deployments/:id/cancel", post(deployment::cancel_deployment))
                .route("/deployments/:id/retry", post(deployment::retry_deployment))
                .route("/deployments/:id/rollback", post(deployment::rollback_deployment))
                .route(
                    "/marketplace/templates",
                    get(marketplace::list_templates).post(marketplace::install_template),
                )
                .route(
                    "/marketplace/templates/:slug",
                    get(marketplace::get_template),
                )
                .route("/marketplace/categories", get(marketplace::list_categories))
                .route(
                    "/services/:id/addons",
                    get(addon::list_addons).post(addon::create_addon),
                )
                .route("/addons/types", get(addon::get_supported_addon_types))
                .route(
                    "/addons/:id",
                    get(addon::get_addon).delete(addon::delete_addon),
                )
                .route("/addons/:id/deprovision", post(addon::deprovision_addon))
                .route("/addons/:id/logs", get(addon::get_addon_logs))
                .route("/admin/users", get(admin::list_users))
                .route("/admin/users/:id", get(admin::get_user).patch(admin::update_user))
                .route("/admin/system/stats", get(admin::system_stats))
                .route("/admin/audit-log", get(admin::search_audit_log))
                .route("/admin/kill-switch", post(admin::kill_switch)),
        )
}

async fn internal_deploy(
    State(_state): State<Arc<AppState>>,
) -> impl axum::response::IntoResponse {
    (
        axum::http::StatusCode::NOT_IMPLEMENTED,
        "internal deploy not yet implemented",
    )
}

async fn internal_heartbeat(
    State(_state): State<Arc<AppState>>,
) -> impl axum::response::IntoResponse {
    (
        axum::http::StatusCode::NOT_IMPLEMENTED,
        "internal heartbeat not yet implemented",
    )
}
