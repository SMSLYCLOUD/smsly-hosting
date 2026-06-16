use axum::{extract::State, http::StatusCode, Json};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

use crate::{middleware::AuthUser, AppState};
use cn_core::entities::{team, team_member};

#[derive(Serialize)]
pub struct TeamResponse {
    pub id: Uuid,
    pub name: String,
}

#[derive(Deserialize)]
pub struct CreateTeamRequest {
    pub name: String,
}

pub async fn list_teams(
    State(state): State<Arc<AppState>>,
    auth_user: AuthUser,
) -> Result<Json<Vec<TeamResponse>>, (StatusCode, String)> {

    // Find all team IDs the user is a member of
    let memberships = team_member::Entity::find()
        .filter(team_member::Column::UserId.eq(auth_user.id))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let team_ids: Vec<Uuid> = memberships.into_iter().map(|m| m.team_id).collect();

    // Fetch the teams
    let teams = team::Entity::find()
        .filter(team::Column::Id.is_in(team_ids))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let response = teams
        .into_iter()
        .map(|t| TeamResponse {
            id: t.id,
            name: t.name,
        })
        .collect();

    Ok(Json(response))
}

pub async fn create_team(
    State(state): State<Arc<AppState>>,
    auth_user: AuthUser,
    Json(payload): Json<CreateTeamRequest>,
) -> Result<(StatusCode, Json<TeamResponse>), (StatusCode, String)> {
    // 2. Create the Team
    let new_team = team::ActiveModel {
        id: Set(Uuid::new_v4()),
        name: Set(payload.name),
        owner_id: Set(auth_user.id),
        created_at: Set(chrono::Utc::now().into()),
        ..Default::default()
    };

    let inserted_team = new_team
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // 3. Create the initial TeamMember record (Make the creator an OWNER)
    let new_member = team_member::ActiveModel {
        team_id: Set(inserted_team.id),
        user_id: Set(auth_user.id),
        role: Set("OWNER".to_string()),
        ..Default::default()
    };

    new_member
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok((
        StatusCode::CREATED,
        Json(TeamResponse {
            id: inserted_team.id,
            name: inserted_team.name,
        }),
    ))
}