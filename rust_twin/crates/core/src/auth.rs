use anyhow::{Context, Result};
use argon2::{
    password_hash::{rand_core::OsRng, PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2,
};
use jsonwebtoken::{decode, encode, DecodingKey, EncodingKey, Header, Validation};
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct Claims {
    pub sub: i32,     // The user ID
    pub exp: usize,   // Expiration timestamp
    pub iat: usize,   // Issued at timestamp
}

pub struct AuthUtils;

impl AuthUtils {
    /// Generates an Argon2 hash from a raw password string.
    pub fn hash_password(password: &str) -> Result<String> {
        let salt = SaltString::generate(&mut OsRng);
        let argon2 = Argon2::default();
        let password_hash = argon2
            .hash_password(password.as_bytes(), &salt)
            .map_err(|e| anyhow::anyhow!("Failed to hash password: {}", e))?
            .to_string();
        Ok(password_hash)
    }

    /// Verifies a raw password string against a stored Argon2 hash.
    pub fn verify_password(password: &str, stored_hash: &str) -> Result<bool> {
        let parsed_hash = PasswordHash::new(stored_hash)
            .map_err(|e| anyhow::anyhow!("Invalid stored password hash format: {}", e))?;

        let argon2 = Argon2::default();
        let is_valid = argon2
            .verify_password(password.as_bytes(), &parsed_hash)
            .is_ok();

        Ok(is_valid)
    }

    /// Generates a JWT token for the given user ID, valid for 24 hours.
    pub fn create_jwt(user_id: i32, secret_key: &str) -> Result<String> {
        let now = chrono::Utc::now().timestamp() as usize;
        // Expire in 24 hours
        let exp = now + (24 * 60 * 60);

        let claims = Claims {
            sub: user_id,
            exp,
            iat: now,
        };

        let token = encode(
            &Header::default(),
            &claims,
            &EncodingKey::from_secret(secret_key.as_ref()),
        )
        .context("Failed to encode JWT token")?;

        Ok(token)
    }

    /// Validates a JWT token string and returns the contained claims (e.g., user_id).
    pub fn decode_jwt(token: &str, secret_key: &str) -> Result<Claims> {
        let mut validation = Validation::default();
        validation.leeway = 60; // 60 seconds of clock skew leeway

        let token_data = decode::<Claims>(
            token,
            &DecodingKey::from_secret(secret_key.as_ref()),
            &validation,
        )
        .context("Failed to decode or validate JWT token")?;

        Ok(token_data.claims)
    }
}