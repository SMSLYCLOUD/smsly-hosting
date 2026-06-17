//! ACME client service — wraps [`instant_acme`] to provide real Let's Encrypt
//! integration (DNS-01 and HTTP-01 challenges, certificate issuance).
//!
//! Uses the Let's Encrypt **staging** directory by default to avoid hitting
//! production rate limits during development. The directory can be overridden
//! by setting the `SMSLY_ACME_DIRECTORY` environment variable (e.g. to
//! `https://acme-v02.api.letsencrypt.org/directory` for production).
//!
//! [`instant_acme`]: https://docs.rs/instant-acme

use base64::Engine;
use instant_acme::{
    Account, ChallengeType, Identifier, LetsEncrypt, NewAccount, NewOrder, Order, OrderStatus,
    RetryPolicy,
};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex};
use thiserror::Error;
use tracing::info;

/// Resolve the ACME directory URL from the `SMSLY_ACME_DIRECTORY` env var,
/// falling back to Let's Encrypt staging.
fn directory_url() -> String {
    std::env::var("SMSLY_ACME_DIRECTORY")
        .unwrap_or_else(|_| LetsEncrypt::Staging.url().to_string())
}

/// Errors produced by the ACME service.
#[derive(Debug, Error)]
pub enum AcmeError {
    #[error("ACME client error: {0}")]
    Acme(#[from] instant_acme::Error),
    #[error("invalid contact email: {0}")]
    InvalidContact(String),
    #[error("no HTTP-01 challenge available for {0}")]
    NoHttp01Challenge(String),
    #[error("no DNS-01 challenge available for {0}")]
    NoDns01Challenge(String),
    #[error("order did not become ready: {0:?}")]
    OrderNotReady(OrderStatus),
    #[error("other: {0}")]
    Other(String),
}

/// HTTP-01 challenge details (RFC 8555 §8.3).
#[derive(Debug, Clone)]
pub struct Http01Challenge {
    pub token: String,
    pub key_authorization: String,
}

/// DNS-01 challenge details (RFC 8555 §8.4).
#[derive(Debug, Clone)]
pub struct Dns01Challenge {
    pub token: String,
    pub dns_value: String,
}

/// Result of a successful certificate issuance.
#[derive(Debug, Clone)]
pub struct Certificate {
    pub cert_chain_pem: String,
    pub private_key_pem: String,
}

/// ACME client backed by the Let's Encrypt staging environment by default.
#[derive(Clone)]
pub struct AcmeClient {
    account: Account,
    directory_url: String,
    contact_email: String,
    /// The RFC 7638 JWK thumbprint of the account key.
    key_thumbprint: String,
}

impl AcmeClient {
    /// Create a new ACME client and register an account on the configured
    /// directory. Uses Let's Encrypt **staging** by default.
    pub async fn new(contact_email: &str) -> Result<Self, AcmeError> {
        if contact_email.is_empty() || !contact_email.contains('@') {
            return Err(AcmeError::InvalidContact(contact_email.to_string()));
        }
        let contact_uri = format!("mailto:{}", contact_email);
        let dir = directory_url();
        info!(directory = %dir, contact = %contact_uri, "creating ACME account");
        let builder = Account::builder()?;
        let (account, _credentials) = builder
            .create(
                &NewAccount {
                    contact: &[contact_uri.as_str()],
                    terms_of_service_agreed: true,
                    only_return_existing: false,
                },
                dir.clone(),
                None,
            )
            .await?;
        let key_thumbprint = account.key_thumbprint().to_string();
        Ok(Self {
            account,
            directory_url: dir,
            contact_email: contact_email.to_string(),
            key_thumbprint,
        })
    }

    pub fn directory_url(&self) -> &str {
        &self.directory_url
    }

    pub fn contact_email(&self) -> &str {
        &self.contact_email
    }

    /// The RFC 7638 JWK thumbprint of the account key.
    pub fn key_thumbprint(&self) -> &str {
        &self.key_thumbprint
    }

    /// Create a new order for `domain`, retrieve the HTTP-01 challenge, store
    /// the `key_authorization` in the in-memory token store, and notify the
    /// ACME server that the challenge is ready to validate.
    pub async fn http01_challenge(&self, domain: &str) -> Result<Http01Challenge, AcmeError> {
        let mut order = self
            .account
            .new_order(&NewOrder::new(&[Identifier::Dns(domain.to_string())]))
            .await?;
        let (token, key_authorization) = extract_http01(&mut order, domain).await?;
        store_token(domain, &token, &key_authorization);
        register_order(domain, order);
        Ok(Http01Challenge {
            token,
            key_authorization,
        })
    }

    /// Create a new order for `domain` and return the DNS-01 challenge details.
    pub async fn dns01_challenge(&self, domain: &str) -> Result<Dns01Challenge, AcmeError> {
        let mut order = self
            .account
            .new_order(&NewOrder::new(&[Identifier::Dns(domain.to_string())]))
            .await?;
        let (token, dns_value) = extract_dns01(&mut order, domain).await?;
        register_order(domain, order);
        Ok(Dns01Challenge { token, dns_value })
    }

    /// Finalize the most recent in-flight order for `domain` and return the
    /// issued certificate. `csr_der` is the DER-encoded CSR.
    pub async fn issue_certificate(
        &self,
        domain: &str,
        csr_der: &[u8],
    ) -> Result<Certificate, AcmeError> {
        let mut order = take_order(domain).ok_or_else(|| {
            AcmeError::Other(format!(
                "no in-flight order for {domain}; call http01_challenge or dns01_challenge first"
            ))
        })?;
        let retry = RetryPolicy::default();
        let status = order.poll_ready(&retry).await?;
        if status != OrderStatus::Ready {
            return Err(AcmeError::OrderNotReady(status));
        }
        order.finalize_csr(csr_der).await?;
        let cert_chain_pem = order.poll_certificate(&retry).await?;
        Ok(Certificate {
            cert_chain_pem,
            private_key_pem: String::new(),
        })
    }
}

async fn extract_http01(order: &mut Order, domain: &str) -> Result<(String, String), AcmeError> {
    let mut stream = order.authorizations();
    while let Some(result) = stream.next().await {
        let mut authz = result?;
        if let Some(mut challenge) = authz.challenge(ChallengeType::Http01) {
            let token = challenge.token.clone();
            let key_authorization = challenge.key_authorization().as_str().to_string();
            challenge.set_ready().await?;
            return Ok((token, key_authorization));
        }
    }
    Err(AcmeError::NoHttp01Challenge(domain.to_string()))
}

async fn extract_dns01(order: &mut Order, domain: &str) -> Result<(String, String), AcmeError> {
    let mut stream = order.authorizations();
    while let Some(result) = stream.next().await {
        let mut authz = result?;
        if let Some(mut challenge) = authz.challenge(ChallengeType::Dns01) {
            let token = challenge.token.clone();
            let dns_value = challenge.key_authorization().dns_value();
            challenge.set_ready().await?;
            return Ok((token, dns_value));
        }
    }
    Err(AcmeError::NoDns01Challenge(domain.to_string()))
}

// ---------------------------------------------------------------------------
// In-memory token store
// ---------------------------------------------------------------------------
// TODO: replace with DB-backed store.

type TokenStore = Arc<Mutex<HashMap<String, String>>>;

static TOKEN_STORE: LazyLock<TokenStore> =
    LazyLock::new(|| Arc::new(Mutex::new(HashMap::new())));

/// Store the `key_authorization` for a `<domain>|<token>` pair.
pub fn store_token(domain: &str, token: &str, key_authorization: &str) {
    let key = format!("{}|{}", domain, token);
    TOKEN_STORE
        .lock()
        .expect("token store mutex poisoned")
        .insert(key, key_authorization.to_string());
}

/// Look up the stored `key_authorization` for a `<domain>|<token>` pair.
pub fn lookup_token(domain: &str, token: &str) -> Option<String> {
    let key = format!("{}|{}", domain, token);
    TOKEN_STORE
        .lock()
        .expect("token store mutex poisoned")
        .get(&key)
        .cloned()
}

// ---------------------------------------------------------------------------
// In-flight order registry
// ---------------------------------------------------------------------------

static PENDING_ORDERS: LazyLock<Mutex<HashMap<String, Order>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

pub fn register_order(domain: &str, order: Order) {
    PENDING_ORDERS
        .lock()
        .expect("pending orders mutex poisoned")
        .insert(domain.to_string(), order);
}

pub fn take_order(domain: &str) -> Option<Order> {
    PENDING_ORDERS
        .lock()
        .expect("pending orders mutex poisoned")
        .remove(domain)
}

// ---------------------------------------------------------------------------
// JWK Thumbprint (RFC 7638)
// ---------------------------------------------------------------------------

/// Compute the RFC 7638 JWK thumbprint of the given **canonical** JWK string.
pub fn compute_jwk_thumbprint(canonical_jwk: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(canonical_jwk.as_bytes());
    let digest = hasher.finalize();
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(digest)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// RFC 7638 §3.1 Example 1 — RSA key thumbprint.
    #[test]
    fn jwk_thumbprint_matches_rfc7638_rsa_example() {
        let canonical_jwk = r#"{"e":"AQAB","kty":"RSA","n":"0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw"}"#;
        let expected = "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs";
        assert_eq!(compute_jwk_thumbprint(canonical_jwk), expected);
    }

    /// `AcmeClient::new` should successfully register an account against the
    /// Let's Encrypt staging environment. Network test, ignored by default.
    #[tokio::test]
    #[ignore = "network test against Let's Encrypt staging"]
    async fn acme_client_new_against_staging() {
        let client = AcmeClient::new("smsly-dev@example.com")
            .await
            .expect("AcmeClient::new should succeed against Let's Encrypt staging");
        assert!(!client.key_thumbprint().is_empty());
        assert!(client.directory_url().contains("staging"));
    }
}
