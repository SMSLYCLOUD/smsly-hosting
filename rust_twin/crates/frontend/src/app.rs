use leptos::*;
use leptos_router::*;
use reqwest::Client;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProjectResponse {
    pub id: String,
    pub name: String,
    pub slug: String,
    pub description: String,
    pub is_default: bool,
    pub owner_id: i32,
}

#[component]
pub fn App() -> impl IntoView {
    view! {
        <Router>
            <main class="min-h-screen bg-gray-50 flex flex-col">
                <nav class="bg-indigo-600 text-white p-4 shadow-md">
                    <div class="container mx-auto flex justify-between items-center">
                        <h1 class="text-xl font-bold">"CloudNeuron"</h1>
                        <div class="space-x-4">
                            <A href="/" class="hover:underline">"Dashboard"</A>
                            <A href="/projects" class="hover:underline">"Projects"</A>
                        </div>
                    </div>
                </nav>

                <div class="container mx-auto flex-grow p-6">
                    <Routes>
                        <Route path="/" view=Dashboard/>
                        <Route path="/projects" view=Projects/>
                        <Route path="/*any" view=NotFound/>
                    </Routes>
                </div>
            </main>
        </Router>
    }
}

#[component]
fn Dashboard() -> impl IntoView {
    view! {
        <div class="space-y-4">
            <h2 class="text-3xl font-bold text-gray-800">"Welcome to CloudNeuron"</h2>
            <p class="text-gray-600">"Your fully integrated Rust-based PaaS dashboard."</p>
        </div>
    }
}

#[component]
fn Projects() -> impl IntoView {
    // 1. Create a reactive signal to hold the HTTP client
    let client = Client::new();

    // 2. Define an asynchronous resource that fetches from our Axum API
    let fetch_projects = create_resource(
        || (),
        move |_| {
            let client = client.clone();
            async move {
                // Point this to the Axum API server running on :8000
                let url = "http://localhost:8000/api/v1/projects";

                let response = client.get(url).send().await;
                match response {
                    Ok(resp) => {
                        if resp.status().is_success() {
                            resp.json::<Vec<ProjectResponse>>().await.ok()
                        } else {
                            None
                        }
                    }
                    Err(_) => None,
                }
            }
        },
    );

    view! {
        <div class="space-y-6">
            <h2 class="text-2xl font-bold text-gray-800">"Your Projects"</h2>

            <Suspense fallback=move || view! { <p>"Loading projects..."</p> }>
                {move || match fetch_projects.get() {
                    None => view! { <p class="text-red-500">"Error loading projects or still fetching."</p> }.into_view(),
                    Some(None) => view! { <p class="text-red-500">"Failed to parse API response."</p> }.into_view(),
                    Some(Some(projects)) => {
                        if projects.is_empty() {
                            view! {
                                <div class="bg-white p-8 rounded-lg shadow-sm border text-center text-gray-500">
                                    "No projects found. Create one to get started."
                                </div>
                            }.into_view()
                        } else {
                            view! {
                                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                                    <For
                                        each=move || projects.clone()
                                        key=|p| p.id.clone()
                                        children=move |p| {
                                            view! {
                                                <div class="bg-white p-6 rounded-lg shadow border border-gray-100 hover:shadow-md transition">
                                                    <h3 class="text-lg font-bold text-indigo-700">{p.name}</h3>
                                                    <p class="text-sm text-gray-500 mb-2">"Slug: " {p.slug}</p>
                                                    <p class="text-gray-700">{p.description}</p>
                                                    <div class="mt-4 text-xs text-gray-400">
                                                        "Owner ID: " {p.owner_id}
                                                    </div>
                                                </div>
                                            }
                                        }
                                    />
                                </div>
                            }.into_view()
                        }
                    }
                }}
            </Suspense>
        </div>
    }
}

#[component]
fn NotFound() -> impl IntoView {
    view! {
        <div class="text-center py-20">
            <h1 class="text-4xl font-bold text-gray-800">"404"</h1>
            <p class="text-xl text-gray-600 mt-4">"Page not found"</p>
            <A href="/" class="text-indigo-600 hover:underline mt-8 inline-block">"Return home"</A>
        </div>
    }
}