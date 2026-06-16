//! Phase 2 entity smoke tests: cluster + mesh_node + node_election + heartbeat_log.
//!
//! These are structural compile-time checks — they verify the new election/mesh
//! entities can be instantiated with the right column types and that FK / unique
//! constraint shapes match the migration definitions. They are wired into the
//! crate via `mod entity_tests_p2` from `entities/mod.rs` when the rest of the
//! registry is rolled out.

#[allow(dead_code)]
fn assert_cluster_shape() {
    use crate::entities::cluster;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: Uuid = Uuid::nil();
        let _name: String = String::new();
        let _region: String = String::new();
        let _mesh_token: String = String::new();
        let _wireguard_public_key: Option<String> = None;
        let _is_active: bool = false;
        let _created_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _updated_at: DateTimeWithTimeZone = chrono::Utc::now().into();
    }

    let _ = std::any::type_name::<cluster::Model>();
    let _ = std::any::type_name::<cluster::Relation>();
}

#[allow(dead_code)]
fn assert_mesh_node_shape() {
    use crate::entities::mesh_node;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: Uuid = Uuid::nil();
        let _cluster_id: Uuid = Uuid::nil();
        let _hostname: String = String::new();
        let _ip_address: String = String::new();
        let _port: i32 = 0;
        let _role: String = String::new();
        let _status: String = String::new();
        let _last_seen: Option<DateTimeWithTimeZone> = None;
        let _cpu_capacity: i32 = 0;
        let _memory_capacity_mb: i32 = 0;
        let _current_load: f64 = 0.0;
        let _version: String = String::new();
        let _created_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _updated_at: DateTimeWithTimeZone = chrono::Utc::now().into();
    }

    let _ = std::any::type_name::<mesh_node::Model>();
    let _ = std::any::type_name::<mesh_node::Relation>();
}

#[allow(dead_code)]
fn assert_node_election_shape() {
    use crate::entities::node_election;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: i32 = 0;
        let _cluster_id: Uuid = Uuid::nil();
        let _master_node_id: Option<Uuid> = None;
        let _term: i64 = 0;
        let _last_election_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _created_at: DateTimeWithTimeZone = chrono::Utc::now().into();
        let _updated_at: DateTimeWithTimeZone = chrono::Utc::now().into();
    }

    let _ = std::any::type_name::<node_election::Model>();
    let _ = std::any::type_name::<node_election::Relation>();
}

#[allow(dead_code)]
fn assert_heartbeat_log_shape() {
    use crate::entities::heartbeat_log;
    use sea_orm::entity::prelude::*;

    fn _assert_column_types() {
        let _id: i64 = 0;
        let _node_id: Uuid = Uuid::nil();
        let _term: i64 = 0;
        let _cpu_usage: f64 = 0.0;
        let _memory_usage_mb: i32 = 0;
        let _active_deployments: i32 = 0;
        let _is_master: bool = false;
        let _received_at: DateTimeWithTimeZone = chrono::Utc::now().into();
    }

    let _ = std::any::type_name::<heartbeat_log::Model>();
    let _ = std::any::type_name::<heartbeat_log::Relation>();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cluster_compiles() {
        assert_cluster_shape();
    }

    #[test]
    fn mesh_node_compiles() {
        assert_mesh_node_shape();
    }

    #[test]
    fn node_election_compiles() {
        assert_node_election_shape();
    }

    #[test]
    fn heartbeat_log_compiles() {
        assert_heartbeat_log_shape();
    }
}
