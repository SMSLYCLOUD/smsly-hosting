//! Exponential backoff retry for webhook delivery.

use std::time::Duration;

pub struct RetryPolicy {
    pub max_attempts: u32,
    pub initial_delay: Duration,
    pub max_delay: Duration,
    pub multiplier: f64,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 5,
            initial_delay: Duration::from_secs(2),
            max_delay: Duration::from_secs(3600),  // 1 hour
            multiplier: 2.0,
        }
    }
}

impl RetryPolicy {
    pub fn delay_for_attempt(&self, attempt: u32) -> Duration {
        let delay_secs = (self.initial_delay.as_secs() as f64) * self.multiplier.powi(attempt as i32);
        let capped = delay_secs.min(self.max_delay.as_secs() as f64);
        Duration::from_secs(capped as u64)
    }
    pub fn should_retry(&self, attempt: u32, response_code: Option<i32>) -> bool {
        if attempt >= self.max_attempts {
            return false;
        }
        // Retry on 5xx and network errors. Don't retry on 4xx (client error).
        match response_code {
            Some(c) if (500..600).contains(&c) => true,
            Some(_) => false,  // 2xx, 3xx, 4xx all "done"
            None => true,      // network error
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_backoff_grows() {
        let p = RetryPolicy::default();
        assert!(p.delay_for_attempt(0) < p.delay_for_attempt(1));
        assert!(p.delay_for_attempt(1) < p.delay_for_attempt(2));
    }

    #[test]
    fn test_backoff_caps() {
        let p = RetryPolicy::default();
        assert!(p.delay_for_attempt(20) <= p.max_delay);
    }

    #[test]
    fn test_retry_5xx() {
        let p = RetryPolicy::default();
        assert!(p.should_retry(0, Some(502)));
        assert!(p.should_retry(0, Some(503)));
    }

    #[test]
    fn test_no_retry_4xx() {
        let p = RetryPolicy::default();
        assert!(!p.should_retry(0, Some(404)));
        assert!(!p.should_retry(0, Some(401)));
    }

    #[test]
    fn test_no_retry_after_max() {
        let p = RetryPolicy::default();
        assert!(!p.should_retry(10, Some(500)));
    }
}
