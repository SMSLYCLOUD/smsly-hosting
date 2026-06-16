use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DataPoint {
    pub timestamp: i64,
    pub value: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnomalyResult {
    pub point: DataPoint,
    pub z_score: f64,
    pub is_anomaly: bool,
}

pub struct ZScoreDetector {
    threshold: f64,
}

impl ZScoreDetector {
    /// Create a new detector. A standard threshold is usually between 2.0 and 3.0.
    pub fn new(threshold: f64) -> Self {
        Self { threshold }
    }

    /// Analyzes a time-series of float values and returns them with their Z-Scores attached.
    /// If the standard deviation is 0 (all values identical), no anomalies are flagged.
    pub fn detect_anomalies(&self, series: &[DataPoint]) -> Vec<AnomalyResult> {
        if series.is_empty() {
            return vec![];
        }

        // 1. Calculate Mean
        let sum: f64 = series.iter().map(|p| p.value).sum();
        let mean = sum / series.len() as f64;

        // 2. Calculate Standard Deviation
        let variance_sum: f64 = series.iter().map(|p| {
            let diff = p.value - mean;
            diff * diff
        }).sum();
        let variance = variance_sum / series.len() as f64;
        let std_dev = variance.sqrt();

        // 3. Compute Z-Scores and flag anomalies
        series.iter().map(|p| {
            let mut z_score = 0.0;
            let mut is_anomaly = false;

            if std_dev > 0.0 {
                z_score = (p.value - mean) / std_dev;
                is_anomaly = z_score.abs() > self.threshold;
            }

            AnomalyResult {
                point: p.clone(),
                z_score,
                is_anomaly,
            }
        }).collect()
    }
}
