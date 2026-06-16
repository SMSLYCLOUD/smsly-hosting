use leptos::*;
use leptos_router::*;
use reqwest::{header, Client};
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
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AuthResponse {
    pub token: String,
    pub user_id: i32,
    pub username: String,
}

// Global Auth State
#[derive(Clone, Debug)]
pub struct AuthState(pub RwSignal<Option<AuthResponse>>);

#[component]
pub fn App() -> impl IntoView {
    // Initialize global authentication state
    let auth_signal = create_rw_signal(None);
    provide_context(AuthState(auth_signal));

    view! {
        <Router>
            <main class="min-h-screen bg-gray-50 flex flex-col">
                <nav class="bg-indigo-600 text-white p-4 shadow-md">
                    <div class="container mx-auto flex justify-between items-center">
                        <h1 class="text-xl font-bold">"Grid"</h1>
                        <div class="space-x-4">
                            <A href="/" class="hover:underline">"Dashboard"</A>
                            <A href="/projects" class="hover:underline">"Projects"</A>
                            <A href="/login" class="hover:underline font-semibold border border-white px-3 py-1 rounded">"Login"</A>
                        </div>
                    </div>
                </nav>

                <div class="container mx-auto flex-grow p-6">
                    <Routes>
                        <Route path="/" view=Dashboard/>
                        <Route path="/login" view=Login/>
                        <Route path="/projects" view=Projects/>
                        <Route path="/*any" view=NotFound/>
                    </Routes>
                </div>
            </main>
        </Router>
    }
}

#[component]
fn Login() -> impl IntoView {
    let auth_state = expect_context::<AuthState>();
    let navigate = use_navigate();

    let (username, set_username) = create_signal(String::new());
    let (password, set_password) = create_signal(String::new());
    let (error_msg, set_error_msg) = create_signal(String::new());

    let login_action = create_action(move |(u, p): &(String, String)| {
        let u = u.clone();
        let p = p.clone();
        let nav = navigate.clone();

        async move {
            let client = Client::new();
            let payload = serde_json::json!({
                "username": u,
                "password": p
            });

            let res = client
                .post("/api/v1/auth/login")
                .json(&payload)
                .send()
                .await;

            match res {
                Ok(resp) => {
                    if resp.status().is_success() {
                        if let Ok(data) = resp.json::<AuthResponse>().await {
                            auth_state.0.set(Some(data));
                            set_error_msg.set(String::new());
                            nav("/projects", Default::default());
                        }
                    } else {
                        set_error_msg.set("Invalid credentials".to_string());
                    }
                }
                Err(_) => set_error_msg.set("Network error".to_string()),
            }
        }
    });

    view! {
        <div class="max-w-md mx-auto mt-10 bg-white p-8 border border-gray-200 rounded-xl shadow-sm">
            <h2 class="text-2xl font-bold text-center mb-6">"Sign In"</h2>

            <Show when=move || !error_msg.get().is_empty()>
                <div class="mb-4 p-3 bg-red-100 text-red-700 rounded text-sm">
                    {error_msg.get()}
                </div>
            </Show>

            <form on:submit=move |ev| {
                ev.prevent_default();
                login_action.dispatch((username.get(), password.get()));
            }>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2">"Username"</label>
                    <input
                        type="text"
                        class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline"
                        on:input=move |ev| set_username.set(event_target_value(&ev))
                        prop:value=username
                        required
                    />
                </div>
                <div class="mb-6">
                    <label class="block text-gray-700 text-sm font-bold mb-2">"Password"</label>
                    <input
                        type="password"
                        class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 mb-3 leading-tight focus:outline-none focus:shadow-outline"
                        on:input=move |ev| set_password.set(event_target_value(&ev))
                        prop:value=password
                        required
                    />
                </div>
                <div class="flex items-center justify-between">
                    <button
                        type="submit"
                        class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline w-full"
                        disabled=move || login_action.pending().get()
                    >
                        {move || if login_action.pending().get() { "Logging in..." } else { "Sign In" }}
                    </button>
                </div>
            </form>
        </div>
    }
}

#[component]
fn Dashboard() -> impl IntoView {
    view! {
        <div class="space-y-4">
            <h2 class="text-3xl font-bold text-gray-800">"Welcome to Grid"</h2>
            <p class="text-gray-600">"Your fully integrated Rust-based PaaS dashboard."</p>
        </div>
    }
}

#[component]
fn Projects() -> impl IntoView {
    let auth_state = expect_context::<AuthState>();
    let client = Client::new();

    let fetch_projects = create_resource(
        move || auth_state.0.get(), // Refetch if auth changes
        move |auth_data| {
            let client = client.clone();
            async move {
                // If not authenticated, return None
                let token = match auth_data {
                    Some(data) => data.token,
                    None => return None,
                };

                let url = "/api/v1/projects";

                let response = client
                    .get(url)
                    .header(header::AUTHORIZATION, format!("Bearer {}", token))
                    .send()
                    .await;

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
                    None => view! {
                        <div class="bg-red-50 p-4 border border-red-200 text-red-700 rounded">
                            "You must be logged in to view projects."
                            <A href="/login" class="ml-2 font-bold hover:underline">"Log in here"</A>
                        </div>
                    }.into_view(),
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
