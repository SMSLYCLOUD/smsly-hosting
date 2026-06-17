use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "deployments_safedeploypolicy")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub team_id: Uuid,                       // FK to team
    #[sea_orm(column_type = "String(StringLen::N(150))")]
    pub name: String,
    pub required_approvers: i32,
    #[sea_orm(column_type = "String(StringLen::N(30))")]
    pub min_approver_role: String,           // "OWNER", "ADMIN", "MEMBER", "VIEWER"
    pub block_on_recent_failures: bool,
    pub max_concurrent_deploys: i32,
    pub is_active: bool,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::team::Entity",
        from = "Column::TeamId",
        to = "super::team::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Team,
}

impl Related<super::team::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Team.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
