use leptos::*;
use serde::{Deserialize, Serialize};

use super::{api_url, build_request, current_token};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ServiceRow {
    pub id: String,
    pub project_id: String,
    pub slug: String,
    pub name: String,
    pub deploy_type: String,
    pub repository_url: Option<String>,
    pub branch: String,
    pub root_directory: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PaginatedServices {
    pub items: Vec<ServiceRow>,
    pub total: i64,
    pub page: u64,
}

#[component]
pub fn AdminServices() -> impl IntoView {
    let client = reqwest::Client::new();
    let url = api_url("/api/v1/services");

    let services = create_resource(
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
                resp.json::<PaginatedServices>().await.map_err(|e| e.to_string())
            }
        },
    );

    view! {
        <div class="space-y-6">
            <h2 class="text-2xl font-bold text-gray-800">"Admin · Services"</h2>

            <Suspense fallback=move || view! { <p>"Loading..."</p> }>
                {move || match services.get() {
                    None => view! { <p>"Loading..."</p> }.into_view(),
                    Some(Err(msg)) => view! { <p class="error">{msg}</p> }.into_view(),
                    Some(Ok(page)) => {
                        if page.items.is_empty() {
                            view! { <p class="text-gray-500">"No services found."</p> }.into_view()
                        } else {
                            view! {
                                <table class="min-w-full bg-white border border-gray-200 rounded">
                                    <thead class="bg-gray-100">
                                        <tr>
                                            <th class="px-4 py-2 text-left">"Name"</th>
                                            <th class="px-4 py-2 text-left">"Slug"</th>
                                            <th class="px-4 py-2 text-left">"Type"</th>
                                            <th class="px-4 py-2 text-left">"Branch"</th>
                                            <th class="px-4 py-2 text-left">"Repository"</th>
                                            <th class="px-4 py-2 text-left">"Project"</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <For
                                            each=move || page.items.clone()
                                            key=|s| s.id.clone()
                                            children=move |s| {
                                                view! {
                                                    <tr class="border-t">
                                                        <td class="px-4 py-2">{s.name}</td>
                                                        <td class="px-4 py-2">{s.slug}</td>
                                                        <td class="px-4 py-2">{s.deploy_type}</td>
                                                        <td class="px-4 py-2">{s.branch}</td>
                                                        <td class="px-4 py-2">
                                                            {s.repository_url.clone().unwrap_or_else(|| "-".to_string())}
                                                        </td>
                                                        <td class="px-4 py-2 text-xs text-gray-500">
                                                            {s.project_id.clone()}
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
