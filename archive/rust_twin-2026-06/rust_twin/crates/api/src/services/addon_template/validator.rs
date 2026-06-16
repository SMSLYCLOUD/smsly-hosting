use super::loader::Template;

pub enum ValidationError {
    InvalidCategory(String),
    InvalidTier(String),
    InvalidImage(String),
    InvalidSlug(String),
}

pub struct TemplateValidator;

impl TemplateValidator {
    pub fn new() -> Self { Self }
    pub fn validate(&self, _t: &Template) -> Result<(), ValidationError> {
        Ok(())
    }
}
