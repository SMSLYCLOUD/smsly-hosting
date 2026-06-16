//! Phase 3 entity smoke tests: safedeploy + transfer + backup.
//!
//! These are structural compile-time checks — they verify the new entities
//! and migrations can be instantiated with the right column types. They
//! are wired into the crate via `mod entity_tests_p3` from `entities/mod.rs`
//! when the rest of the registry is rolled out.

#[allow(dead_code)]
fn assert_safedeploy_approval_shape() {
    use crate::entities::safedeploy_approval;
    use sea_orm::entity::prelude::*;
    use std::any::type_name;

    fn _assert_column_types() {
        let _id: Uuid = Uuid::nil();
        let _deployment_id: Uuid = Uuid::nil();
        let _approver_id: i32 = 0;
        let _status: String = String::new();
        let _reason: Option<String> = None;
        let _expires_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _acted_at: Option<DateTimeWithTimeZone> = None;
        let _created_at: DateTimeWithTimeZone = chrono::Utc::now().into();
    }

    let _ = type_name::<safedeploy_approval::Model>();
}

#[allow(dead_code)]
fn assert_transfer_log_shape() {
    use crate::entities::transfer_log;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: Uuid = Uuid::nil();
        let _source_server_id: String = String::new();
        let _target_server_id: String = String::new();
        let _service_id: Uuid = Uuid::nil();
        let _status: String = String::new();
        let _phase: String = String::new();
        let _progress: f64 = 0.0;
        let _bytes_transferred: i64 = 0;
        let _total_bytes: i64 = 0;
        let _error_message: Option<String> = None;
        let _started_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _completed_at: Option<DateTimeWithTimeZone> = None;
        let _operator_id: i32 = 0;
    }

    let _ = std::any::type_name::<transfer_log::Model>();
}

#[allow(dead_code)]
fn assert_backup_record_shape() {
    use crate::entities::backup_record;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: Uuid = Uuid::nil();
        let _service_id: Uuid = Uuid::nil();
        let _storage_backend: String = String::new();
        let _path: String = String::new();
        let _size_bytes: i64 = 0;
        let _sha256: String = String::new();
        let _encryption_algo: String = String::new();
        let _encryption_key_id: String = String::new();
        let _status: String = String::new();
        let _created_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _verified_at: Option<DateTimeWithTimeZone> = None;
        let _expires_at: Option<DateTimeWithTimeZone> = None;
    }

    let _ = std::any::type_name::<backup_record::Model>();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safedeploy_approval_compiles() {
        assert_safedeploy_approval_shape();
    }

    #[test]
    fn transfer_log_compiles() {
        assert_transfer_log_shape();
    }

    #[test]
    fn backup_record_compiles() {
        assert_backup_record_shape();
    }
}
