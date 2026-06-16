use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct AddDeploymentStatusCheck;

#[async_trait::async_trait]
impl MigrationTrait for AddDeploymentStatusCheck {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        // Add a CHECK constraint that status is one of the allowed values.
        manager.get_connection()
            .execute_unprepared(
                "ALTER TABLE deployments_deployment ADD CONSTRAINT chk_deployment_status \
                 CHECK (status IN ('AWAITING_APPROVAL','QUEUED','BUILDING','BUILD_FAILED','DEPLOYING',\
                       'DEPLOY_FAILED','RUNNING','UNHEALTHY','STOPPING','STOPPED','ROLLING_OUT',\
                       'ROLLED_BACK','REMOVED'))"
            )
            .await?;
        Ok(())
    }
    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager.get_connection()
            .execute_unprepared("ALTER TABLE deployments_deployment DROP CONSTRAINT IF EXISTS chk_deployment_status")
            .await?;
        Ok(())
    }
}
