//! Smoke tests for billing entities: `Plan`, `Subscription`, `Invoice`.
//!
//! These tests are mostly compile-time type checks that pin the shape of
//! each entity (derives, column types, FK relations) plus a few runtime
//! serde round-trip checks. They do not require a live database.

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};
    use sea_orm::entity::prelude::*;
    use serde::{Deserialize, Serialize};
    use uuid::Uuid;

    use crate::entities::invoice::{Entity as InvoiceEntity, Model as Invoice};
    use crate::entities::plan::{Entity as PlanEntity, Model as Plan};
    use crate::entities::subscription::{Entity as SubscriptionEntity, Model as Subscription};
    use crate::entities::user::Entity as UserEntity;

    fn assert_serde<T: Serialize + for<'de> Deserialize<'de>>() {}
    fn assert_clone_eq<T: Clone + Eq>() {}

    #[test]
    fn plan_model_derives_required_traits() {
        assert_serde::<Plan>();
        assert_clone_eq::<Plan>();
    }

    #[test]
    fn subscription_model_derives_required_traits() {
        assert_serde::<Subscription>();
        assert_clone_eq::<Subscription>();
    }

    #[test]
    fn invoice_model_derives_required_traits() {
        assert_serde::<Invoice>();
        assert_clone_eq::<Invoice>();
    }

    fn sample_plan() -> Plan {
        Plan {
            id: 1,
            code: "free".to_string(),
            name: "Community".to_string(),
            description: Some("Free tier for individual developers".to_string()),
            max_services: 3,
            max_team_members: 1,
            max_domains_per_service: 1,
            monthly_price_cents: 0,
            yearly_price_cents: 0,
            stripe_price_id: None,
            cryptomus_plan_id: None,
            is_active: true,
            created_at: Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap(),
            updated_at: Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap(),
        }
    }

    fn sample_subscription() -> Subscription {
        Subscription {
            id: Uuid::nil(),
            user_id: 7,
            plan_id: 1,
            status: "active".to_string(),
            started_at: Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap(),
            current_period_start: Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap(),
            current_period_end: Utc.with_ymd_and_hms(2025, 2, 1, 0, 0, 0).unwrap(),
            cancel_at: None,
            cancelled_at: None,
            stripe_subscription_id: None,
            cryptomus_subscription_id: None,
            payment_provider: "stripe".to_string(),
            created_at: Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap(),
            updated_at: Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap(),
        }
    }

    fn sample_invoice() -> Invoice {
        Invoice {
            id: Uuid::nil(),
            user_id: 7,
            subscription_id: Some(Uuid::nil()),
            amount_cents: 2900,
            currency: "USD".to_string(),
            status: "open".to_string(),
            invoice_number: "INV-2025-0001".to_string(),
            description: Some("Pro plan - January 2025".to_string()),
            period_start: Some(Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap()),
            period_end: Some(Utc.with_ymd_and_hms(2025, 2, 1, 0, 0, 0).unwrap()),
            due_date: Some(Utc.with_ymd_and_hms(2025, 1, 15, 0, 0, 0).unwrap()),
            paid_at: None,
            stripe_invoice_id: None,
            cryptomus_invoice_id: None,
            created_at: Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap(),
            updated_at: Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap(),
        }
    }

    #[test]
    fn plan_serde_roundtrip() {
        let plan = sample_plan();
        let json = serde_json::to_string(&plan).expect("serialize plan");
        let back: Plan = serde_json::from_str(&json).expect("deserialize plan");
        assert_eq!(plan, back);
    }

    #[test]
    fn subscription_serde_roundtrip() {
        let sub = sample_subscription();
        let json = serde_json::to_string(&sub).expect("serialize subscription");
        let back: Subscription =
            serde_json::from_str(&json).expect("deserialize subscription");
        assert_eq!(sub, back);
    }

    #[test]
    fn invoice_serde_roundtrip() {
        let inv = sample_invoice();
        let json = serde_json::to_string(&inv).expect("serialize invoice");
        let back: Invoice = serde_json::from_str(&json).expect("deserialize invoice");
        assert_eq!(inv, back);
    }

    #[test]
    fn plan_code_uniqueness_via_schema_metadata() {
        // The plan code column is declared unique in both the entity and
        // the migration. Pin the schema-level intent here.
        let col_def = <PlanEntity as EntityTrait>::Column::Code.def();
        assert!(
            col_def.is_unique(),
            "billing_plan.code must carry a UNIQUE constraint"
        );
    }

    #[test]
    fn invoice_number_uniqueness_via_schema_metadata() {
        let col_def = <InvoiceEntity as EntityTrait>::Column::InvoiceNumber.def();
        assert!(
            col_def.is_unique(),
            "billing_invoice.invoice_number must carry a UNIQUE constraint"
        );
    }

    #[test]
    fn subscription_belongs_to_user_and_plan() {
        use crate::entities::plan::Entity as PlanEntityRef;
        let _ = <SubscriptionEntity as Related<UserEntity>>::to();
        let _ = <SubscriptionEntity as Related<PlanEntityRef>>::to();
    }

    #[test]
    fn invoice_belongs_to_user_and_subscription() {
        use crate::entities::subscription::Entity as SubscriptionEntityRef;
        let _ = <InvoiceEntity as Related<UserEntity>>::to();
        let _ = <InvoiceEntity as Related<SubscriptionEntityRef>>::to();
    }

    #[test]
    fn plan_has_many_subscriptions() {
        use crate::entities::subscription::Entity as SubscriptionEntityRef;
        let _ = <PlanEntity as Related<SubscriptionEntityRef>>::to();
    }
}
