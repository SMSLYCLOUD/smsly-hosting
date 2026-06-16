//! Marketplace HTTP handlers — list, search, install addon templates.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use uuid::Uuid;
use std::sync::Arc;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::addon_template;
use cn_core::entities::addon_template::Entity as AddonTemplateEntity;

#[derive(Debug, Serialize)]
pub struct TemplateResponse {
    pub id: Uuid,
    pub slug: String,
    pub name: String,
    pub description: String,
    pub category: String,
    pub image: String,
    pub default_port: i32,
    pub tier: String,
    pub documentation_url: Option<String>,
    pub is_active: bool,
}

impl From<addon_template::Model> for TemplateResponse {
    fn from(t: addon_template::Model) -> Self {
        Self {
            id: t.id,
            slug: t.slug,
            name: t.name,
            description: t.description,
            category: t.category,
            image: t.image,
            default_port: t.default_port,
            tier: t.tier,
            documentation_url: t.documentation_url,
            is_active: t.is_active,
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct ListTemplatesQuery {
    pub category: Option<String>,
    pub tier: Option<String>,
    pub search: Option<String>,
}

pub async fn list_templates(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Query(q): Query<ListTemplatesQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let mut query = AddonTemplateEntity::find()
        .filter(addon_template::Column::IsActive.eq(true));
    if let Some(cat) = q.category {
        query = query.filter(addon_template::Column::Category.eq(cat));
    }
    if let Some(t) = q.tier {
        query = query.filter(addon_template::Column::Tier.eq(t));
    }
    let mut templates = query
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    if let Some(search) = q.search {
        let s = search.to_lowercase();
        templates.retain(|t| {
            t.name.to_lowercase().contains(&s) || t.description.to_lowercase().contains(&s)
        });
    }
    let resp: Vec<TemplateResponse> = templates.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

pub async fn get_template(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(slug): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let t = AddonTemplateEntity::find()
        .filter(addon_template::Column::Slug.eq(&slug))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, format!("template '{}' not found", slug)))?;
    Ok(Json(TemplateResponse::from(t)))
}

#[derive(Debug, Serialize)]
pub struct CategorySummary {
    pub category: String,
    pub count: i64,
}

pub async fn list_categories(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let templates = AddonTemplateEntity::find()
        .filter(addon_template::Column::IsActive.eq(true))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let mut counts: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    for t in &templates {
        *counts.entry(t.category.clone()).or_insert(0) += 1;
    }
    let mut summary: Vec<CategorySummary> = counts
        .into_iter()
        .map(|(category, count)| CategorySummary { category, count })
        .collect();
    summary.sort_by(|a, b| a.category.cmp(&b.category));
    Ok(Json(summary))
}

#[derive(Debug, Deserialize)]
pub struct InstallTemplateBody {
    pub service_id: Uuid,
    pub template_slug: String,
    pub name: String,
    pub env_overrides: Option<serde_json::Value>,
}

pub async fn install_template(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Json(body): Json<InstallTemplateBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let tpl = AddonTemplateEntity::find()
        .filter(addon_template::Column::Slug.eq(&body.template_slug))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| {
            (
                StatusCode::NOT_FOUND,
                format!("template '{}' not found", body.template_slug),
            )
        })?;
    if !tpl.is_active {
        return Err((
            StatusCode::GONE,
            "template is no longer active".to_string(),
        ));
    }
    if tpl.tier != "community" {
        // In production, check the user's subscription tier here.
    }
    use cn_core::entities::service::Entity as ServiceEntity;
    let svc = ServiceEntity::find_by_id(body.service_id)
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "service not found".to_string()))?;
    use cn_core::entities::addon;
    let now: chrono::DateTime<chrono::FixedOffset> = chrono::Utc::now().into();
    let new_addon = addon::ActiveModel {
        id: Set(Uuid::new_v4()),
        project_id: Set(Some(svc.project_id)),
        service_id: Set(body.service_id),
        name: Set(body.name),
        addon_type: Set(tpl.slug.to_uppercase()),
        status: Set("PROVISIONING".to_string()),
        connection_url: Set(None),
        container_id: Set(None),
        created_at: Set(now),
        updated_at: Set(now),
    };
    let inserted = new_addon
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((
        StatusCode::ACCEPTED,
        Json(serde_json::json!({
            "addon_id": inserted.id,
            "template_slug": tpl.slug,
            "template_name": tpl.name,
            "status": "PROVISIONING",
        })),
    ))
}
