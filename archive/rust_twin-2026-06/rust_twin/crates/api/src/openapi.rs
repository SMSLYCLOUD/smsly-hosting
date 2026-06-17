//! Hand-written OpenAPI 3.1 spec handler.
//!
//! The spec is committed as `openapi.json` and embedded at compile time. It is
//! served at `GET /openapi.json` so the existing Next.js frontend (and any
//! future codegen tooling) can target the rust_twin without depending on utoipa
//! or any codegen pipeline.
//!
//! The file is intentionally a *subset* of the full Axum router — it only
//! documents the paths the frontend actually calls. Add entries here when the
//! frontend grows a new integration; do not turn this into a utoipa dump.

use axum::{
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};

pub const OPENAPI_JSON: &str = include_str!("openapi.json");

pub async fn openapi_spec() -> Response {
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "application/json")],
        OPENAPI_JSON,
    )
        .into_response()
}
