//! Team HTTP handlers — teams, members, ownership transfer.

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::Utc;
use std::sync::Arc;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::{team, team_member};
use cn_core::entities::team::Entity as TeamEntity;
use cn_core::entities::team_member::Entity as TeamMemberEntity;
use cn_core::entities::user::Entity as UserEntity;

#[derive(Debug, Serialize)]
pub struct TeamResponse {
    pub id: Uuid,
    pub name: String,
    pub owner_id: i32,
    pub member_count: i32,
    pub created_at: chrono::DateTime<Utc>,
}

#[derive(Debug, Deserialize)]
pub struct CreateTeamBody {
    pub name: String,
}

pub async fn list_my_teams(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let memberships = TeamMemberEntity::find()
        .filter(team_member::Column::UserId.eq(auth.id))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let team_ids: Vec<Uuid> = memberships.into_iter().map(|m| m.team_id).collect();
    let teams = TeamEntity::find()
        .filter(team::Column::Id.is_in(team_ids))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let mut resp = Vec::with_capacity(teams.len());
    for t in teams {
        let count = TeamMemberEntity::find()
            .filter(team_member::Column::TeamId.eq(t.id))
            .all(&state.db)
            .await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
            .len() as i32;
        resp.push(TeamResponse {
            id: t.id,
            name: t.name,
            owner_id: t.owner_id,
            member_count: count,
            created_at: t.created_at.with_timezone(&Utc),
        });
    }
    Ok(Json(resp))
}

pub async fn create_team(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Json(body): Json<CreateTeamBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let now = Utc::now();
    let new_team = team::ActiveModel {
        id: Set(Uuid::new_v4()),
        name: Set(body.name),
        owner_id: Set(auth.id),
        created_at: Set(now.into()),
        ..Default::default()
    };
    let inserted = new_team
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let member = team_member::ActiveModel {
        team_id: Set(inserted.id),
        user_id: Set(auth.id),
        role: Set("OWNER".to_string()),
        ..Default::default()
    };
    member
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((
        StatusCode::CREATED,
        Json(TeamResponse {
            id: inserted.id,
            name: inserted.name,
            owner_id: inserted.owner_id,
            member_count: 1,
            created_at: inserted.created_at.with_timezone(&Utc),
        }),
    ))
}

pub async fn get_team(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let _ = TeamMemberEntity::find()
        .filter(team_member::Column::TeamId.eq(id))
        .filter(team_member::Column::UserId.eq(auth.id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::FORBIDDEN, "not a team member".to_string()))?;
    let t = TeamEntity::find_by_id(id)
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "team not found".to_string()))?;
    let count = TeamMemberEntity::find()
        .filter(team_member::Column::TeamId.eq(t.id))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .len() as i32;
    Ok(Json(TeamResponse {
        id: t.id,
        name: t.name,
        owner_id: t.owner_id,
        member_count: count,
        created_at: t.created_at.with_timezone(&Utc),
    }))
}

pub async fn delete_team(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let t = TeamEntity::find_by_id(id)
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "team not found".to_string()))?;
    if t.owner_id != auth.id {
        return Err((
            StatusCode::FORBIDDEN,
            "only the owner can delete the team".to_string(),
        ));
    }
    TeamMemberEntity::delete_many()
        .filter(team_member::Column::TeamId.eq(id))
        .exec(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    TeamEntity::delete_by_id(id)
        .exec(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Debug, Serialize)]
pub struct MemberResponse {
    pub id: i64,
    pub team_id: Uuid,
    pub user_id: i32,
    pub username: String,
    pub email: String,
    pub role: String,
}

pub async fn list_team_members(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path(team_id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let _ = TeamMemberEntity::find()
        .filter(team_member::Column::TeamId.eq(team_id))
        .filter(team_member::Column::UserId.eq(auth.id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::FORBIDDEN, "not a team member".to_string()))?;
    let members = TeamMemberEntity::find()
        .filter(team_member::Column::TeamId.eq(team_id))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let mut resp = Vec::with_capacity(members.len());
    for m in members {
        let u = UserEntity::find_by_id(m.user_id)
            .one(&state.db)
            .await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
            .map(|u| (u.username, u.email))
            .unwrap_or_else(|| (String::new(), String::new()));
        resp.push(MemberResponse {
            id: m.id,
            team_id: m.team_id,
            user_id: m.user_id,
            username: u.0,
            email: u.1,
            role: m.role,
        });
    }
    Ok(Json(resp))
}

#[derive(Debug, Deserialize)]
pub struct AddMemberBody {
    pub user_id: i32,
    pub role: String,
}

pub async fn add_team_member(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path(team_id): Path<Uuid>,
    Json(body): Json<AddMemberBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let requester_member = TeamMemberEntity::find()
        .filter(team_member::Column::TeamId.eq(team_id))
        .filter(team_member::Column::UserId.eq(auth.id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::FORBIDDEN, "not a team member".to_string()))?;
    if requester_member.role != "OWNER" && requester_member.role != "ADMIN" {
        return Err((
            StatusCode::FORBIDDEN,
            "only owners/admins can add members".to_string(),
        ));
    }
    if UserEntity::find_by_id(body.user_id)
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .is_none()
    {
        return Err((
            StatusCode::NOT_FOUND,
            format!("user {} not found", body.user_id),
        ));
    }
    if TeamMemberEntity::find()
        .filter(team_member::Column::TeamId.eq(team_id))
        .filter(team_member::Column::UserId.eq(body.user_id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .is_some()
    {
        return Err((
            StatusCode::CONFLICT,
            "user is already a team member".to_string(),
        ));
    }
    let new_member = team_member::ActiveModel {
        team_id: Set(team_id),
        user_id: Set(body.user_id),
        role: Set(body.role),
        ..Default::default()
    };
    let inserted = new_member
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let u = UserEntity::find_by_id(body.user_id)
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .unwrap();
    Ok((
        StatusCode::CREATED,
        Json(MemberResponse {
            id: inserted.id,
            team_id: inserted.team_id,
            user_id: inserted.user_id,
            username: u.username,
            email: u.email,
            role: inserted.role,
        }),
    ))
}

pub async fn remove_team_member(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path((team_id, user_id)): Path<(Uuid, i32)>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let requester = TeamMemberEntity::find()
        .filter(team_member::Column::TeamId.eq(team_id))
        .filter(team_member::Column::UserId.eq(auth.id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::FORBIDDEN, "not a team member".to_string()))?;
    if requester.role != "OWNER" && requester.role != "ADMIN" {
        return Err((
            StatusCode::FORBIDDEN,
            "only owners/admins can remove members".to_string(),
        ));
    }
    let target = TeamMemberEntity::find()
        .filter(team_member::Column::TeamId.eq(team_id))
        .filter(team_member::Column::UserId.eq(user_id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "member not found".to_string()))?;
    if target.role == "OWNER" {
        return Err((
            StatusCode::FORBIDDEN,
            "cannot remove the team owner".to_string(),
        ));
    }
    TeamMemberEntity::delete_by_id(target.id)
        .exec(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn transfer_ownership(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path((team_id, new_owner_id)): Path<(Uuid, i32)>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let t = TeamEntity::find_by_id(team_id)
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "team not found".to_string()))?;
    if t.owner_id != auth.id {
        return Err((
            StatusCode::FORBIDDEN,
            "only the current owner can transfer".to_string(),
        ));
    }
    let mut active: team::ActiveModel = t.into();
    active.owner_id = Set(new_owner_id);
    active
        .update(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    for uid in [auth.id, new_owner_id] {
        if let Some(m) = TeamMemberEntity::find()
            .filter(team_member::Column::TeamId.eq(team_id))
            .filter(team_member::Column::UserId.eq(uid))
            .one(&state.db)
            .await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        {
            let new_role = if uid == new_owner_id { "OWNER" } else { "ADMIN" };
            let mut active_m: team_member::ActiveModel = m.into();
            active_m.role = Set(new_role.to_string());
            active_m
                .update(&state.db)
                .await
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
        }
    }
    Ok(Json(serde_json::json!({ "status": "transferred" })))
}
