use leptos::*;
use serde::{Deserialize, Serialize};

use super::{api_url, build_request, current_token};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeploymentRow {
    pub id: String,
    pub service_id: String,
    pub commit_hash: String,
    pub status: String,
    pub status_enum: String,
    pub is_rollback: bool,
    pub requester_id: Option<i32>,
    pub created_at: String,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PaginatedDeployments {
    pub items: Vec<DeploymentRow>,
    pub total: i64,
    pub page: u64,
}

#[component]
pub fn AdminDeployments() -> impl IntoView {
    let client = reqwest::Client::new();
    let url = api_url("/api/v1/deployments");

    let deployments = create_resource(
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
                if !status.is_success() {
                    return Err(format!("HTTP {}", status.as_u16()));
                }
                resp.json::<PaginatedDeployments>().await.map_err(|e| e.to_string())
            }
        },
    );

    view! {
        <div class="space-y-6">
            <h2 class="text-2xl font-bold text-gray-800">"Admin · Deployments"</h2>

            <Suspense fallback=move || view! { <p>"Loading..."</p> }>
                {move || match deployments.get() {
                    None => view! { <p>"Loading..."</p> }.into_view(),
                    Some(Err(msg)) => view! { <p class="error">{msg}</p> }.into_view(),
                    Some(Ok(page)) => {
                        if page.items.is_empty() {
                            view! { <p class="text-gray-500">"No deployments found."</p> }.into_view()
                        } else {
                            view! {
                                <table class="min-w-full bg-white border border-gray-200 rounded">
                                    <thead class="bg-gray-100">
                                        <tr>
                                            <th class="px-4 py-2 text-left">"Status"</th>
                                            <th class="px-4 py-2 text-left">"Service"</th>
                                            <th class="px-4 py-2 text-left">"Commit"</th>
                                            <th class="px-4 py-2 text-left">"Rollback"</th>
                                            <th class="px-4 py-2 text-left">"Requester"</th>
                                            <th class="px-4 py-2 text-left">"Created"</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <For
                                            each=move || page.items.clone()
                                            key=|d| d.id.clone()
                                            children=move |d| {
                                                view! {
                                                    <tr class="border-t">
                                                        <td class="px-4 py-2">{d.status_enum.clone()}</td>
                                                        <td class="px-4 py-2 text-xs text-gray-500">
                                                            {d.service_id.clone()}
                                                        </td>
                                                        <td class="px-4 py-2 font-mono text-xs">
                                                            {d.commit_hash.clone()}
                                                        </td>
                                                        <td class="px-4 py-2">{d.is_rollback.to_string()}</td>
                                                        <td class="px-4 py-2">
                                                            {d.requester_id.map(|i| i.to_string()).unwrap_or_else(|| "-".to_string())}
                                                        </td>
                                                        <td class="px-4 py-2 text-xs">{d.created_at.clone()}</td>
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
