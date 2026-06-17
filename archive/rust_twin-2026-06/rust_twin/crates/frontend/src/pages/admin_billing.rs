use leptos::*;
use serde::{Deserialize, Serialize};

use super::{api_url, build_request, current_token};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlanInfo {
    pub id: i32,
    pub code: String,
    pub name: String,
    pub monthly_price_cents: i32,
    pub yearly_price_cents: i32,
    pub is_active: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Subscription {
    pub id: String,
    pub user_id: i32,
    pub plan: PlanInfo,
    pub status: String,
    pub started_at: String,
    pub current_period_end: String,
    pub payment_provider: String,
    pub cancel_at: Option<String>,
    pub cancelled_at: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct InvoiceRow {
    pub id: String,
    pub invoice_number: String,
    pub amount_cents: i32,
    pub currency: String,
    pub status: String,
    pub period_start: Option<String>,
    pub period_end: Option<String>,
    pub due_date: Option<String>,
    pub paid_at: Option<String>,
}

#[component]
pub fn AdminBilling() -> impl IntoView {
    let client = reqwest::Client::new();
    let sub_url = api_url("/api/v1/billing/subscription");
    let inv_url = api_url("/api/v1/billing/invoices");
    let client_a = client.clone();
    let client_b = client.clone();

    let sub = create_resource(
        move || current_token(),
        move |token| {
            let client = client_a.clone();
            let url = sub_url.clone();
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
                resp.json::<Subscription>().await.map_err(|e| e.to_string())
            }
        },
    );

    let invoices = create_resource(
        move || current_token(),
        move |token| {
            let client = client_b.clone();
            let url = inv_url.clone();
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
                resp.json::<Vec<InvoiceRow>>().await.map_err(|e| e.to_string())
            }
        },
    );

    view! {
        <div class="space-y-8">
            <h2 class="text-2xl font-bold text-gray-800">"Admin · Billing"</h2>

            <section>
                <h3 class="text-xl font-semibold text-gray-700 mb-3">"Subscription"</h3>
                <Suspense fallback=move || view! { <p>"Loading..."</p> }>
                    {move || match sub.get() {
                        None => view! { <p>"Loading..."</p> }.into_view(),
                        Some(Err(msg)) => view! { <p class="error">{msg}</p> }.into_view(),
                        Some(Ok(s)) => view! {
                            <div class="bg-white p-6 rounded border border-gray-200">
                                <p class="text-sm text-gray-500">"Plan: "
                                    <span class="font-bold text-gray-800">{s.plan.name.clone()}</span>
                                </p>
                                <p class="text-sm text-gray-500">"Code: " {s.plan.code.clone()}</p>
                                <p class="text-sm text-gray-500">"Status: " {s.status.clone()}</p>
                                <p class="text-sm text-gray-500">"Provider: " {s.payment_provider.clone()}</p>
                                <p class="text-sm text-gray-500">"Started: " {s.started_at.clone()}</p>
                                <p class="text-sm text-gray-500">"Renews: " {s.current_period_end.clone()}</p>
                                <p class="text-sm text-gray-500">"Monthly: $"
                                    {(s.plan.monthly_price_cents as f64 / 100.0).to_string()}
                                </p>
                                <p class="text-sm text-gray-500">"Yearly: $"
                                    {(s.plan.yearly_price_cents as f64 / 100.0).to_string()}
                                </p>
                            </div>
                        }.into_view(),
                    }}
                </Suspense>
            </section>

            <section>
                <h3 class="text-xl font-semibold text-gray-700 mb-3">"Invoices"</h3>
                <Suspense fallback=move || view! { <p>"Loading..."</p> }>
                    {move || match invoices.get() {
                        None => view! { <p>"Loading..."</p> }.into_view(),
                        Some(Err(msg)) => view! { <p class="error">{msg}</p> }.into_view(),
                        Some(Ok(list)) => {
                            if list.is_empty() {
                                view! { <p class="text-gray-500">"No invoices yet."</p> }.into_view()
                            } else {
                                view! {
                                    <table class="min-w-full bg-white border border-gray-200 rounded">
                                        <thead class="bg-gray-100">
                                            <tr>
                                                <th class="px-4 py-2 text-left">"Number"</th>
                                                <th class="px-4 py-2 text-left">"Amount"</th>
                                                <th class="px-4 py-2 text-left">"Status"</th>
                                                <th class="px-4 py-2 text-left">"Period"</th>
                                                <th class="px-4 py-2 text-left">"Paid"</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            <For
                                                each=move || list.clone()
                                                key=|i| i.id.clone()
                                                children=move |i| {
                                                    let amount = (i.amount_cents as f64) / 100.0;
                                                    view! {
                                                        <tr class="border-t">
                                                            <td class="px-4 py-2 font-mono text-xs">{i.invoice_number}</td>
                                                            <td class="px-4 py-2">
                                                                {i.currency.clone()} " " {amount.to_string()}
                                                            </td>
                                                            <td class="px-4 py-2">{i.status}</td>
                                                            <td class="px-4 py-2 text-xs text-gray-500">
                                                                {i.period_start.clone().unwrap_or_default()} " — "
                                                                {i.period_end.clone().unwrap_or_default()}
                                                            </td>
                                                            <td class="px-4 py-2 text-xs">
                                                                {i.paid_at.clone().unwrap_or_else(|| "-".to_string())}
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
            </section>
        </div>
    }
}
