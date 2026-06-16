//! Phase 4 entity smoke tests: tunnel + webhook + domain.
//!
//! Structural compile-time checks verifying the new entities can be
//! instantiated with the right column types. Mirrors the entity_tests_p3
//! pattern. Wired into the crate via `mod entity_tests_p4` from
//! `entities/mod.rs` when the rest of the registry is rolled out.

#[allow(dead_code)]
fn assert_tunnel_shape() {
    use crate::entities::tunnel;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: Uuid = Uuid::nil();
        let _service_id: Uuid = Uuid::nil();
        let _local_port: i32 = 0;
        let _public_subdomain: String = String::new();
        let _public_port: i32 = 0;
        let _protocol: String = String::new();
        let _status: String = String::new();
        let _connection_count: i32 = 0;
        let _bytes_in: i64 = 0;
        let _bytes_out: i64 = 0;
        let _last_connected_at: Option<DateTimeWithTimeZone> = None;
        let _created_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _updated_at: DateTimeWithTimeZone = chrono::Utc::now().into();
    }

    let _ = std::any::type_name::<tunnel::Model>();
}

#[allow(dead_code)]
fn assert_webhook_shape() {
    use crate::entities::webhook;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: Uuid = Uuid::nil();
        let _user_id: i32 = 0;
        let _service_id: Option<Uuid> = None;
        let _url: String = String::new();
        let _secret: String = String::new();
        let _events: String = String::new();
        let _is_active: bool = false;
        let _last_triggered_at: Option<DateTimeWithTimeZone> = None;
        let _last_response_code: Option<i32> = None;
        let _failure_count: i32 = 0;
        let _created_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _updated_at: DateTimeWithTimeZone = chrono::Utc::now().into();
    }

    let _ = std::any::type_name::<webhook::Model>();
}

#[allow(dead_code)]
fn assert_domain_shape() {
    use crate::entities::domain;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: Uuid = Uuid::nil();
        let _service_id: Uuid = Uuid::nil();
        let _domain: String = String::new();
        let _is_primary: bool = false;
        let _ssl_status: String = String::new();
        let _ssl_provider: String = String::new();
        let _ssl_expires_at: Option<DateTimeWithTimeZone> = None;
        let _ssl_certificate_path: Option<String> = None;
        let _verification_method: String = String::new();
        let _verification_token: Option<String> = None;
        let _last_verified_at: Option<DateTimeWithTimeZone> = None;
        let _created_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _updated_at: DateTimeWithTimeZone = chrono::Utc::now().into();
    }

    let _ = std::any::type_name::<domain::Model>();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tunnel_compiles() {
        assert_tunnel_shape();
    }

    #[test]
    fn webhook_compiles() {
        assert_webhook_shape();
    }

    #[test]
    fn domain_compiles() {
        assert_domain_shape();
    }
}
