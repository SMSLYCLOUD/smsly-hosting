//! Request-duration tracking middleware (Gap 11 — Observability).
//!
//! Wraps every request, records the wall-clock duration in nanoseconds into
//! `crate::TOTAL_REQUEST_DURATION_NANOS`, and increments
//! `crate::TOTAL_REQUESTS`. The values are exposed via the Prometheus
//! `smsly_http_request_duration_seconds_avg` gauge computed in
//! `handlers::observability::metrics`.
//!
//! All operations use `Relaxed` ordering — these counters are advisory
//! metrics and never participate in synchronization, so the relaxed ordering
//! is sufficient and cheaper than the stronger variants.

use axum::{
    extract::Request,
    middleware::Next,
    response::Response,
};
use std::time::Instant;

pub async fn request_duration_middleware(req: Request, next: Next) -> Response {
    let start = Instant::now();
    let resp = next.run(req).await;
    let nanos = start.elapsed().as_nanos();
    crate::inc_request_duration(nanos);
    crate::inc_total_requests();
    resp
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{TOTAL_REQUESTS, TOTAL_REQUEST_DURATION_NANOS};
    use axum::{
        body::Body,
        http::{Request as HttpRequest, StatusCode},
        middleware::from_fn,
        response::IntoResponse,
        routing::get,
        Router,
    };
    use std::sync::atomic::Ordering;
    use tower::ServiceExt;

    async fn noop() -> impl IntoResponse {
        (StatusCode::OK, "ok")
    }

    #[tokio::test]
    async fn test_request_duration_middleware_increments_counters() {
        let nanos_before = TOTAL_REQUEST_DURATION_NANOS.load(Ordering::Relaxed);
        let count_before = TOTAL_REQUESTS.load(Ordering::Relaxed);

        let app = Router::new()
            .route("/ping", get(noop))
            .layer(from_fn(request_duration_middleware));

        let req = HttpRequest::builder()
            .uri("/ping")
            .body(Body::empty())
            .unwrap();
        let resp = app.oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);

        let nanos_after = TOTAL_REQUEST_DURATION_NANOS.load(Ordering::Relaxed);
        let count_after = TOTAL_REQUESTS.load(Ordering::Relaxed);

        assert_eq!(count_after, count_before + 1, "TOTAL_REQUESTS must increment");
        assert!(
            nanos_after > nanos_before,
            "TOTAL_REQUEST_DURATION_NANOS must grow"
        );
    }
}
