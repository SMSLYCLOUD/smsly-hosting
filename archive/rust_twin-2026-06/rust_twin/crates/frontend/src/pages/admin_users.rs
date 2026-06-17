use leptos::*;
use serde::{Deserialize, Serialize};

use super::{api_url, build_request, current_token};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AdminUser {
    pub id: i32,
    pub username: String,
    pub email: String,
    pub is_active: bool,
    pub is_staff: bool,
    pub is_superuser: bool,
    pub project_count: i64,
    pub service_count: i64,
    pub deployment_count: i64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PaginatedUsers {
    pub items: Vec<AdminUser>,
    pub total: i64,
    pub page: u64,
    pub per_page: u64,
}

#[component]
pub fn AdminUsers() -> impl IntoView {
    let client = reqwest::Client::new();
    let url = api_url("/api/v1/admin/users");

    let users = create_resource(
        move || current_token(),
        move |token| {
            let client = client.clone();
            let url = url.clone();
            async move {
                if token.is_none() {
                    return Err("not authenticated".to_string());
                }
                let builder = build_request(&client, reqwest::Method::GET, &url)
                    .map_err(|e| e.to_string())?;
                let resp = builder.send().await.map_err(|e| e.to_string())?;
                let status = resp.status();
                let body: PaginatedUsers = resp.json().await.map_err(|e| e.to_string())?;
                if !status.is_success() {
                    return Err(format!("HTTP {}", status.as_u16()));
                }
                Ok(body)
            }
        },
    );

    view! {
        <div class="space-y-6">
            <h2 class="text-2xl font-bold text-gray-800">"Admin · Users"</h2>

            <Suspense fallback=move || view! { <p>"Loading..."</p> }>
                {move || match users.get() {
                    None => view! { <p>"Loading..."</p> }.into_view(),
                    Some(Err(msg)) => view! {
                        <p class="error">{msg}</p>
                    }.into_view(),
                    Some(Ok(page)) => {
                        if page.items.is_empty() {
                            view! {
                                <p class="text-gray-500">"No users found."</p>
                            }.into_view()
                        } else {
                            view! {
                                <table class="min-w-full bg-white border border-gray-200 rounded">
                                    <thead class="bg-gray-100">
                                        <tr>
                                            <th class="px-4 py-2 text-left">"ID"</th>
                                            <th class="px-4 py-2 text-left">"Username"</th>
                                            <th class="px-4 py-2 text-left">"Email"</th>
                                            <th class="px-4 py-2 text-left">"Active"</th>
                                            <th class="px-4 py-2 text-left">"Staff"</th>
                                            <th class="px-4 py-2 text-left">"Super"</th>
                                            <th class="px-4 py-2 text-left">"Projects"</th>
                                            <th class="px-4 py-2 text-left">"Services"</th>
                                            <th class="px-4 py-2 text-left">"Deploys"</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <For
                                            each=move || page.items.clone()
                                            key=|u| u.id
                                            children=move |u| {
                                                view! {
                                                    <tr class="border-t">
                                                        <td class="px-4 py-2">{u.id}</td>
                                                        <td class="px-4 py-2">{u.username}</td>
                                                        <td class="px-4 py-2">{u.email}</td>
                                                        <td class="px-4 py-2">{u.is_active.to_string()}</td>
                                                        <td class="px-4 py-2">{u.is_staff.to_string()}</td>
                                                        <td class="px-4 py-2">{u.is_superuser.to_string()}</td>
                                                        <td class="px-4 py-2">{u.project_count}</td>
                                                        <td class="px-4 py-2">{u.service_count}</td>
                                                        <td class="px-4 py-2">{u.deployment_count}</td>
                                                    </tr>
                                                }
                                            }
                                        />
                                    </tbody>
                                </table>
                            }.into_view()
                        }
                    }
                }}
            </Suspense>
        </div>
    }
}
