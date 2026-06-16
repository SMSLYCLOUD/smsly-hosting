//! Seeder for the most common addon templates.

pub struct TemplateSeeder<'a> {
    pub db: &'a (),
}

impl<'a> TemplateSeeder<'a> {
    pub async fn seed_defaults(&self) -> Result<usize, String> {
        Ok(0)
    }
}
