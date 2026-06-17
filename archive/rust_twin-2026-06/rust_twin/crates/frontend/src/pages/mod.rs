pub mod admin_audit;
pub mod admin_billing;
pub mod admin_deployments;
pub mod admin_services;
pub mod admin_users;

use leptos::*;

use crate::app::AuthState;

pub(crate) fn api_url(path: &str) -> String {
    let base = origin();
    if base.is_empty() {
        path.to_string()
    } else {
        format!("{}{}", base, path)
    }
}

#[cfg(target_arch = "wasm32")]
fn origin() -> String {
    if let Some(window) = leptos::window().location().origin().ok() {
        window
    } else {
        String::new()
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn origin() -> String {
    String::new()
}

pub(crate) fn current_token() -> Option<String> {
    let auth = expect_context::<AuthState>();
    auth.0.get().map(|a| a.token)
}

pub(crate) fn build_request(
    client: &reqwest::Client,
    method: reqwest::Method,
    url: &str,
) -> Result<reqwest::RequestBuilder, String> {
    let token = current_token().ok_or_else(|| "not authenticated".to_string())?;
    let mut builder = client.request(method, url);
    if !token.is_empty() {
        builder = builder.bearer_auth(token);
    }
    Ok(builder)
}
