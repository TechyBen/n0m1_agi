//! A causal sensor-learning experiment, independent of n0m1's Python daemons.
pub mod engine;
pub mod learning;
pub mod model;
pub mod sensor;
pub mod store;

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
