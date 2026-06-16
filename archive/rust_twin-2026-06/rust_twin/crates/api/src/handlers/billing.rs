//! Billing HTTP handlers — plans, subscriptions, invoices, license gate.

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use chrono::Utc;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::{plan, subscription, invoice, platform_license};

#[derive(Debug, Serialize)]
pub struct PlanResponse {
    pub id: i32,
    pub code: String,
    pub name: String,
    pub description: Option<String>,
    pub max_services: i32,
    pub max_team_members: i32,
    pub max_domains_per_service: i32,
    pub monthly_price_cents: i32,
    pub yearly_price_cents: i32,
    pub is_active: bool,
}

impl From<plan::Model> for PlanResponse {
    fn from(p: plan::Model) -> Self {
        Self {
            id: p.id,
            code: p.code,
            name: p.name,
            description: p.description,
            max_services: p.max_services,
            max_team_members: p.max_team_members,
            max_domains_per_service: p.max_domains_per_service,
            monthly_price_cents: p.monthly_price_cents,
            yearly_price_cents: p.yearly_price_cents,
            is_active: p.is_active,
        }
    }
}

pub async fn list_plans(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let plans = plan::Entity::find()
        .filter(plan::Column::IsActive.eq(true))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<PlanResponse> = plans.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

#[derive(Debug, Serialize)]
pub struct SubscriptionResponse {
    pub id: uuid::Uuid,
    pub user_id: i32,
    pub plan: PlanResponse,
    pub status: String,
    pub started_at: chrono::DateTime<Utc>,
    pub current_period_end: chrono::DateTime<Utc>,
    pub payment_provider: String,
    pub cancel_at: Option<chrono::DateTime<Utc>>,
    pub cancelled_at: Option<chrono::DateTime<Utc>>,
}

pub async fn get_my_subscription(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let sub = subscription::Entity::find()
        .filter(subscription::Column::UserId.eq(auth.id))
        .filter(subscription::Column::Status.eq("active"))
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    match sub {
        Some(s) => {
            let p = plan::Entity::find_by_id(s.plan_id).one(&state.db).await
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
                .ok_or_else(|| (StatusCode::INTERNAL_SERVER_ERROR, "plan not found".to_string()))?;
            Ok(Json(SubscriptionResponse {
                id: s.id,
                user_id: s.user_id,
                plan: PlanResponse::from(p),
                status: s.status,
                started_at: s.started_at.into(),
                current_period_end: s.current_period_end.into(),
                payment_provider: s.payment_provider,
                cancel_at: s.cancel_at.map(Into::into),
                cancelled_at: s.cancelled_at.map(Into::into),
            }))
        }
        None => {
            let free = plan::Entity::find()
                .filter(plan::Column::Code.eq("free"))
                .one(&state.db).await
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
                .ok_or_else(|| (StatusCode::INTERNAL_SERVER_ERROR, "free plan not seeded".to_string()))?;
            Ok(Json(SubscriptionResponse {
                id: uuid::Uuid::new_v4(),
                user_id: auth.id,
                plan: PlanResponse::from(free),
                status: "active".to_string(),
                started_at: Utc::now(),
                current_period_end: Utc::now() + chrono::Duration::days(365 * 100),
                payment_provider: "system".to_string(),
                cancel_at: None,
                cancelled_at: None,
            }))
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct UpgradeBody {
    pub target_tier: String,
    pub payment_id: String,
    pub payment_provider: String,
}

pub async fn upgrade_subscription(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Json(body): Json<UpgradeBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let target_plan = plan::Entity::find()
        .filter(plan::Column::Code.eq(&body.target_tier))
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, format!("plan '{}' not found", body.target_tier)))?;
    let existing = subscription::Entity::find()
        .filter(subscription::Column::UserId.eq(auth.id))
        .filter(subscription::Column::Status.eq("active"))
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let now = Utc::now();
    let period_end = now + chrono::Duration::days(30);
    let saved = if let Some(existing) = existing {
        let mut active: subscription::ActiveModel = existing.into();
        active.plan_id = Set(target_plan.id);
        active.status = Set("active".to_string());
        if body.payment_provider == "stripe" {
            active.stripe_subscription_id = Set(Some(body.payment_id.clone()));
        } else {
            active.cryptomus_subscription_id = Set(Some(body.payment_id.clone()));
        }
        active.payment_provider = Set(body.payment_provider.clone());
        active.current_period_start = Set(now.into());
        active.current_period_end = Set(period_end.into());
        active.updated_at = Set(now.into());
        active.update(&state.db).await
    } else {
        let new_sub = subscription::ActiveModel {
            id: Set(uuid::Uuid::new_v4()),
            user_id: Set(auth.id),
            plan_id: Set(target_plan.id),
            status: Set("active".to_string()),
            started_at: Set(now.into()),
            current_period_start: Set(now.into()),
            current_period_end: Set(period_end.into()),
            cancel_at: Set(None),
            cancelled_at: Set(None),
            stripe_subscription_id: Set(if body.payment_provider == "stripe" { Some(body.payment_id.clone()) } else { None }),
            cryptomus_subscription_id: Set(if body.payment_provider == "cryptomus" { Some(body.payment_id.clone()) } else { None }),
            payment_provider: Set(body.payment_provider.clone()),
            created_at: Set(now.into()),
            updated_at: Set(now.into()),
        };
        new_sub.insert(&state.db).await
    }.map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({
        "subscription_id": saved.id,
        "plan": target_plan.code,
        "status": "active",
    })))
}

pub async fn cancel_subscription(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let sub = subscription::Entity::find()
        .filter(subscription::Column::UserId.eq(auth.id))
        .filter(subscription::Column::Status.eq("active"))
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "no active subscription".to_string()))?;
    let mut active: subscription::ActiveModel = sub.into();
    active.status = Set("cancelled".to_string());
    active.cancel_at = Set(Some(Utc::now().into()));
    active.cancelled_at = Set(Some(Utc::now().into()));
    active.updated_at = Set(Utc::now().into());
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({ "status": "cancelled" })))
}

#[derive(Debug, Serialize)]
pub struct InvoiceResponse {
    pub id: uuid::Uuid,
    pub invoice_number: String,
    pub amount_cents: i32,
    pub currency: String,
    pub status: String,
    pub period_start: Option<chrono::DateTime<Utc>>,
    pub period_end: Option<chrono::DateTime<Utc>>,
    pub due_date: Option<chrono::DateTime<Utc>>,
    pub paid_at: Option<chrono::DateTime<Utc>>,
}

impl From<invoice::Model> for InvoiceResponse {
    fn from(i: invoice::Model) -> Self {
        Self {
            id: i.id,
            invoice_number: i.invoice_number,
            amount_cents: i.amount_cents,
            currency: i.currency,
            status: i.status,
            period_start: i.period_start.map(Into::into),
            period_end: i.period_end.map(Into::into),
            due_date: i.due_date.map(Into::into),
            paid_at: i.paid_at.map(Into::into),
        }
    }
}

pub async fn list_my_invoices(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let invoices = invoice::Entity::find()
        .filter(invoice::Column::UserId.eq(auth.id))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<InvoiceResponse> = invoices.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

pub async fn get_license(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
) -> Result<Json<platform_license::Model>, (StatusCode, String)> {
    let license = platform_license::Entity::find()
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    match license {
        Some(l) => Ok(Json(l)),
        None => Ok(Json(platform_license::Model {
            id: 0,
            license_key: "".to_string(),
            tier: "community".to_string(),
            license_data: "".to_string(),
            is_valid: true,
            last_validated: None,
            validation_error: "".to_string(),
            licensed_to: "".to_string(),
            instance_id: "".to_string(),
            expires_at: None,
            max_services: 3,
            max_team_members: 1,
            payment_provider: "".to_string(),
            subscription_id: "".to_string(),
            created_at: Utc::now().into(),
            updated_at: Utc::now().into(),
        })),
    }
}

#[derive(Debug, Deserialize)]
pub struct UpgradeLicenseBody {
    pub target_tier: String,
    pub payment_id: String,
}

pub async fn upgrade_license(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Json(body): Json<UpgradeLicenseBody>,
) -> Result<Json<platform_license::Model>, (StatusCode, String)> {
    let target_tier = body.target_tier.to_lowercase();
    if target_tier != "pro" && target_tier != "enterprise" {
        return Err((StatusCode::BAD_REQUEST, "invalid target tier".to_string()));
    }
    let existing_opt = platform_license::Entity::find()
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let now = Utc::now();
    let expires_at = now + chrono::Duration::days(30);
    let active = if let Some(existing) = existing_opt {
        let mut m: platform_license::ActiveModel = existing.into();
        m.tier = Set(target_tier);
        m.subscription_id = Set(body.payment_id.clone());
        m.expires_at = Set(Some(expires_at.into()));
        m.is_valid = Set(true);
        m.updated_at = Set(now.into());
        m.update(&state.db).await
    } else {
        let new_m = platform_license::ActiveModel {
            id: Set(0),
            license_key: Set("".to_string()),
            tier: Set(target_tier),
            license_data: Set("".to_string()),
            is_valid: Set(true),
            last_validated: Set(None),
            validation_error: Set("".to_string()),
            licensed_to: Set("".to_string()),
            instance_id: Set("".to_string()),
            expires_at: Set(Some(expires_at.into())),
            max_services: Set(1000),
            max_team_members: Set(1000),
            payment_provider: Set("stripe".to_string()),
            subscription_id: Set(body.payment_id.clone()),
            created_at: Set(now.into()),
            updated_at: Set(now.into()),
        };
        new_m.insert(&state.db).await
    }.map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(active))
}
