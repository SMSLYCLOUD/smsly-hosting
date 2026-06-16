//! Business-logic service layer.
//!
//! Each module provides high-level operations (create, list, update, delete,
//! domain-specific actions) for a single entity or related set of entities.
//! Routes (in crates/api/) call into these services; services call into the
//! entities (in crates/core/src/entities/) via sea-orm.

pub mod plan;
pub mod subscription;
pub mod invoice;
pub mod cluster;
pub mod mesh;
pub mod election;
pub mod safedeploy;
pub mod transfer;
pub mod backup;
pub mod tunnel;
pub mod webhook;
pub mod domain;
pub mod sso;
pub mod addon_template;
