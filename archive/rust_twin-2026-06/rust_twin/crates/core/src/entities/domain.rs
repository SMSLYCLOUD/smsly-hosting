use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "deployments_domain")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub service_id: Uuid,                  // FK
    pub domain: String,                    // "app.example.com"
    pub is_primary: bool,
    pub ssl_status: String,                // "pending", "provisioning", "active", "failed"
    pub ssl_provider: String,              // "letsencrypt", "custom"
    pub ssl_expires_at: Option<DateTimeWithTimeZone>,
    pub ssl_certificate_path: Option<String>,
    pub verification_method: String,       // "http-01", "dns-01", "tls-alpn-01"
    pub verification_token: Option<String>, // for HTTP-01 challenges
    pub last_verified_at: Option<DateTimeWithTimeZone>,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::service::Entity",
        from = "Column::ServiceId",
        to = "super::service::Column::Id"
    )]
    Service,
}

impl ActiveModelBehavior for ActiveModel {}
