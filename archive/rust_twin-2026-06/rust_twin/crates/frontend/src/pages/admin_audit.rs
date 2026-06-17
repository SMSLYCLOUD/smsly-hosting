use leptos::*;
use serde::{Deserialize, Serialize};

use super::{api_url, build_request, current_token};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AuditLogEntry {
    pub id: i64,
    pub timestamp: String,
    pub actor_id: Option<i32>,
    pub actor_username: Option<String>,
    pub action: String,
    pub target_type: String,
    pub target_id: String,
    pub ip_address: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AuditLogResponse {
    pub items: Vec<AuditLogEntry>,
    pub total: i64,
    #[serde(default)]
    pub note: Option<String>,
}

#[component]
pub fn AdminAuditLog() -> impl IntoView {
    let client = reqwest::Client::new();
    let url = api_url("/api/v1/admin/audit-log");

    let entries = create_resource(
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
                resp.json::<AuditLogResponse>().await.map_err(|e| e.to_string())
            }
        },
    );

    view! {
        <div class="space-y-6">
            <h2 class="text-2xl font-bold text-gray-800">"Admin · Audit Log"</h2>

            <Suspense fallback=move || view! { <p>"Loading..."</p> }>
                {move || match entries.get() {
                    None => view! { <p>"Loading..."</p> }.into_view(),
                    Some(Err(msg)) => view! { <p class="error">{msg}</p> }.into_view(),
                    Some(Ok(resp)) => {
                        if resp.items.is_empty() {
                            view! {
                                <div class="bg-yellow-50 p-4 border border-yellow-200 rounded">
                                    <p class="text-gray-700">"No audit log entries."</p>
                                    {resp.note.clone().map(|n| view! {
                                        <p class="text-sm text-gray-500 mt-2">{n}</p>
                                    })}
                                </div>
                            }.into_view()
                        } else {
                            view! {
                                <table class="min-w-full bg-white border border-gray-200 rounded">
                                    <thead class="bg-gray-100">
                                        <tr>
                                            <th class="px-4 py-2 text-left">"Time"</th>
                                            <th class="px-4 py-2 text-left">"Actor"</th>
                                            <th class="px-4 py-2 text-left">"Action"</th>
                                            <th class="px-4 py-2 text-left">"Target"</th>
                                            <th class="px-4 py-2 text-left">"IP"</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <For
                                            each=move || resp.items.clone()
                                            key=|e| e.id
                                            children=move |e| {
                                                view! {
                                                    <tr class="border-t">
                                                        <td class="px-4 py-2 text-xs">{e.timestamp.clone()}</td>
                                                        <td class="px-4 py-2">
                                                            {e.actor_username.clone().unwrap_or_else(|| "-".to_string())}
                                                        </td>
                                                        <td class="px-4 py-2">{e.action.clone()}</td>
                                                        <td class="px-4 py-2 text-xs text-gray-500">
                                                            {e.target_type.clone()} " · " {e.target_id.clone()}
                                                        </td>
                                                        <td class="px-4 py-2 text-xs">
                                                            {e.ip_address.clone().unwrap_or_else(|| "-".to_string())}
                                                        </td>
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
