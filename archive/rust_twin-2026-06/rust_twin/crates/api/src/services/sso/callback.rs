//! OAuth callback handler — skeleton.
//!
//! When fully implemented (B4), this will exchange the authorization
//! code for a token set, fetch the remote user profile, and link the
//! SMSLY user to the remote account via the `socialaccount_*` tables.
//!
//! Mirrors the security model from `apps.deployments.views_integrations`:
//! - state is single-use and bound to the initiating user (CSRF)
//! - an existing SocialAccount cannot be silently re-assigned to a
//!   different user (account-takeover prevention)

use std::sync::Arc;

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::IntoResponse,
};
use sea_orm::DatabaseConnection;
use serde::Deserialize;

use super::bitbucket::BitbucketProvider;
use super::github::GitHubProvider;
use super::gitlab::GitLabProvider;
use super::provider::OAuthProvider;

#[derive(Deserialize)]
pub struct CallbackQuery {
    pub code: String,
    pub state: String,
    pub provider: String,
}

#[derive(Clone)]
pub struct AppState {
    pub db: Arc<DatabaseConnection>,
    pub github: Arc<GitHubProvider>,
    pub gitlab: Arc<GitLabProvider>,
    pub bitbucket: Arc<BitbucketProvider>,
    /// CSRF state expected for this flow (resolved from session/signed cookie).
    pub expected_state: String,
}

impl AppState {
    /// Resolve a provider implementation by name.
    ///
    /// Accepts both `bitbucket` (rust_twin convention) and
    /// `bitbucket_oauth2` (Django allauth convention) for backwards
    /// compatibility with existing rows in `socialaccount_socialaccount`.
    pub fn provider_for(&self, name: &str) -> Option<Arc<dyn OAuthProvider>> {
        match name {
            "github" => Some(self.github.clone() as Arc<dyn OAuthProvider>),
            "gitlab" => Some(self.gitlab.clone() as Arc<dyn OAuthProvider>),
            "bitbucket" | "bitbucket_oauth2" => {
                Some(self.bitbucket.clone() as Arc<dyn OAuthProvider>)
            }
            _ => None,
        }
    }
}

pub async fn oauth_callback(
    State(_state): State<AppState>,
    Query(_q): Query<CallbackQuery>,
) -> impl IntoResponse {
    (StatusCode::NOT_IMPLEMENTED, "not yet implemented")
}
