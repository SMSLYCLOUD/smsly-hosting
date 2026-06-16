use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "deployments_safedeployapproval")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub deployment_id: Uuid,                // FK
    pub approver_id: i32,                  // FK to user
    pub status: String,                    // "pending", "approved", "rejected", "expired"
    pub reason: Option<String>,            // approver's note
    pub expires_at: DateTimeWithTimeZone,  // approvals expire
    pub acted_at: Option<DateTimeWithTimeZone>,
    pub created_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::deployment::Entity",
        from = "Column::DeploymentId",
        to = "super::deployment::Column::Id"
    )]
    Deployment,
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::ApproverId",
        to = "super::user::Column::Id"
    )]
    Approver,
}

impl ActiveModelBehavior for ActiveModel {}
