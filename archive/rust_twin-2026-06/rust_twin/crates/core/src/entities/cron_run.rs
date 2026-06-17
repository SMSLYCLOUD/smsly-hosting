use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "cron_run")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub cron_id: Uuid,
    pub started_at: DateTimeWithTimeZone,
    #[sea_orm(nullable)]
    pub completed_at: Option<DateTimeWithTimeZone>,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub status: String,
    #[sea_orm(nullable)]
    pub exit_code: Option<i32>,
    #[sea_orm(column_type = "Text", nullable)]
    pub log_text: Option<String>,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::cron::Entity",
        from = "Column::CronId",
        to = "super::cron::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Cron,
}

impl Related<super::cron::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Cron.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
