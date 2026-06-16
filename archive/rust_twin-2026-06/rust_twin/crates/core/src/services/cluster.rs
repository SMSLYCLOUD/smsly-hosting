//! Cluster + mesh node service — manages the master/node mesh.

use sea_orm::{ColumnTrait, DatabaseConnection, EntityTrait, QueryFilter, Set};
use uuid::Uuid;
use chrono::Utc;
use thiserror::Error;

use crate::entities::{cluster, mesh_node, node_election, heartbeat_log};
use crate::entities::cluster::Entity as ClusterEntity;
use crate::entities::mesh_node::Entity as MeshNodeEntity;
use crate::entities::node_election::Entity as NodeElectionEntity;
use crate::entities::heartbeat_log::Entity as HeartbeatLogEntity;

#[derive(Debug, Error)]
pub enum ClusterError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("cluster not found: {0}")]
    ClusterNotFound(uuid::Uuid),
    #[error("node not found: {0}")]
    NodeNotFound(uuid::Uuid),
    #[error("invalid state: {0}")]
    InvalidState(String),
}

pub struct ClusterService {
    pub db: DatabaseConnection,
}

impl ClusterService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list_clusters(&self) -> Result<Vec<cluster::Model>, ClusterError> {
        let clusters = ClusterEntity::find()
            .filter(cluster::Column::IsActive.eq(true))
            .all(&self.db).await?;
        Ok(clusters)
    }

    pub async fn create_cluster(
        &self,
        name: String,
        region: String,
        mesh_token: String,
    ) -> Result<cluster::Model, ClusterError> {
        let now = Utc::now();
        let new_cluster = cluster::ActiveModel {
            id: Set(Uuid::new_v4()),
            name: Set(name),
            region: Set(region),
            mesh_token: Set(mesh_token),
            wireguard_public_key: Set(None),
            is_active: Set(true),
            created_at: Set(now),
            updated_at: Set(now),
        };
        Ok(new_cluster.insert(&self.db).await?)
    }

    pub async fn register_node(
        &self,
        cluster_id: Uuid,
        hostname: String,
        ip_address: String,
        port: i32,
        role: &str,  // "master" or "worker"
        cpu_capacity: i32,
        memory_capacity_mb: i32,
        version: String,
    ) -> Result<mesh_node::Model, ClusterError> {
        // Verify cluster exists
        ClusterEntity::find_by_id(cluster_id).one(&self.db).await?
            .ok_or(ClusterError::ClusterNotFound(cluster_id))?;
        let now = Utc::now();
        let new_node = mesh_node::ActiveModel {
            id: Set(Uuid::new_v4()),
            cluster_id: Set(cluster_id),
            hostname: Set(hostname),
            ip_address: Set(ip_address),
            port: Set(port),
            role: Set(role.to_string()),
            status: Set("online".to_string()),
            last_seen: Set(Some(now)),
            cpu_capacity: Set(cpu_capacity),
            memory_capacity_mb: Set(memory_capacity_mb),
            current_load: Set(0),
            version: Set(version),
            created_at: Set(now),
            updated_at: Set(now),
        };
        Ok(new_node.insert(&self.db).await?)
    }

    pub async fn record_heartbeat(
        &self,
        node_id: Uuid,
        cpu_usage: i32,
        active_deployments: i32,
        is_master: bool,
        term: i64,
    ) -> Result<(), ClusterError> {
        let now = Utc::now();
        let hb = heartbeat_log::ActiveModel {
            id: Set(0),  // auto-increment; Set(0) is the sea-orm way
            node_id: Set(node_id),
            term: Set(term),
            cpu_usage: Set(cpu_usage),
            memory_usage_mb: Set(0),
            active_deployments: Set(active_deployments),
            is_master: Set(is_master),
            received_at: Set(now),
        };
        hb.insert(&self.db).await?;
        // Update node's last_seen + current_load
        if let Some(node) = MeshNodeEntity::find_by_id(node_id).one(&self.db).await? {
            let mut active: mesh_node::ActiveModel = node.into();
            active.last_seen = Set(Some(now));
            active.current_load = Set(cpu_usage);
            active.updated_at = Set(now);
            active.update(&self.db).await?;
        }
        Ok(())
    }

    /// Elect a master node via simple "highest uptime wins" algorithm.
    /// (In production, use Raft; this is a simplified version for parity.)
    pub async fn elect_master(&self, cluster_id: Uuid, term: i64) -> Result<Option<mesh_node::Model>, ClusterError> {
        let online_nodes: Vec<mesh_node::Model> = MeshNodeEntity::find()
            .filter(mesh_node::Column::ClusterId.eq(cluster_id))
            .filter(mesh_node::Column::Status.eq("online"))
            .all(&self.db).await?;
        if online_nodes.is_empty() { return Ok(None); }
        // Pick the node with the most recent heartbeat as master
        let master = online_nodes.into_iter()
            .max_by_key(|n| n.last_seen)
            .unwrap();
        // Record the election
        let now = Utc::now();
        let election = node_election::ActiveModel {
            id: Set(0),
            cluster_id: Set(cluster_id),
            master_node_id: Set(Some(master.id)),
            term: Set(term),
            last_election_at: Set(now),
            created_at: Set(now),
            updated_at: Set(now),
        };
        election.insert(&self.db).await?;
        Ok(Some(master))
    }

    pub async fn get_current_master(&self, cluster_id: Uuid) -> Result<Option<mesh_node::Model>, ClusterError> {
        let election = NodeElectionEntity::find()
            .filter(node_election::Column::ClusterId.eq(cluster_id))
            .one(&self.db).await?;
        if let Some(election) = election {
            if let Some(master_id) = election.master_node_id {
                let node = MeshNodeEntity::find_by_id(master_id).one(&self.db).await?;
                return Ok(node);
            }
        }
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        let e = ClusterError::InvalidState("no master".into());
        assert!(e.to_string().contains("no master"));
    }
}
