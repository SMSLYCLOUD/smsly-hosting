use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct CreatePlans;

#[async_trait::async_trait]
impl MigrationTrait for CreatePlans {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Plan::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Plan::Id)
                            .integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(Plan::Code)
                            .string_len(50)
                            .not_null()
                            .unique_key(),
                    )
                    .col(ColumnDef::new(Plan::Name).string_len(100).not_null())
                    .col(ColumnDef::new(Plan::Description).text().null())
                    .col(
                        ColumnDef::new(Plan::MaxServices)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Plan::MaxTeamMembers)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Plan::MaxDomainsPerService)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Plan::MonthlyPriceCents)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Plan::YearlyPriceCents)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(ColumnDef::new(Plan::StripePriceId).string_len(100).null())
                    .col(
                        ColumnDef::new(Plan::CryptomusPlanId)
                            .string_len(100)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Plan::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(Plan::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Plan::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .exec_stmt(
                Query::insert()
                    .into_table(Plan::Table)
                    .columns([
                        Plan::Code,
                        Plan::Name,
                        Plan::Description,
                        Plan::MaxServices,
                        Plan::MaxTeamMembers,
                        Plan::MaxDomainsPerService,
                        Plan::MonthlyPriceCents,
                        Plan::YearlyPriceCents,
                        Plan::StripePriceId,
                        Plan::CryptomusPlanId,
                        Plan::IsActive,
                        Plan::CreatedAt,
                        Plan::UpdatedAt,
                    ])
                    .values_panic([
                        "free".into(),
                        "Community".into(),
                        Some("Free tier for individual developers").into(),
                        3i32.into(),
                        1i32.into(),
                        1i32.into(),
                        0i32.into(),
                        0i32.into(),
                        None::<String>.into(),
                        None::<String>.into(),
                        true.into(),
                        Expr::current_timestamp().into(),
                        Expr::current_timestamp().into(),
                    ])
                    .values_panic([
                        "pro".into(),
                        "Pro Tier".into(),
                        Some("For growing teams and production workloads").into(),
                        20i32.into(),
                        5i32.into(),
                        10i32.into(),
                        2900i32.into(),
                        29000i32.into(),
                        None::<String>.into(),
                        None::<String>.into(),
                        true.into(),
                        Expr::current_timestamp().into(),
                        Expr::current_timestamp().into(),
                    ])
                    .values_panic([
                        "enterprise".into(),
                        "Enterprise".into(),
                        Some("Custom limits, dedicated support, and SLAs").into(),
                        100i32.into(),
                        50i32.into(),
                        100i32.into(),
                        29900i32.into(),
                        299000i32.into(),
                        None::<String>.into(),
                        None::<String>.into(),
                        true.into(),
                        Expr::current_timestamp().into(),
                        Expr::current_timestamp().into(),
                    ])
                    .to_owned(),
            )
            .await?;

        Ok(())
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Plan::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Plan {
    #[sea_orm(iden = "billing_plan")]
    Table,
    Id,
    Code,
    Name,
    Description,
    MaxServices,
    MaxTeamMembers,
    MaxDomainsPerService,
    MonthlyPriceCents,
    YearlyPriceCents,
    StripePriceId,
    CryptomusPlanId,
    IsActive,
    CreatedAt,
    UpdatedAt,
}
