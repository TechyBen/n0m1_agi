//! Bounded training and promotion rules. The controller is a heuristic, not a
//! statistical drift detector or a claim that every surprising event is learnable.
use crate::model::{surprise, Example, Models, Net, Rng, Scope};
use serde::{Deserialize, Serialize};
use std::{collections::VecDeque, time::Instant};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Config {
    pub horizon: u64,
    pub warmup: u64,
    pub cooldown: u64,
    pub validation_samples: usize,
    pub low_steps: usize,
    pub high_steps: usize,
    pub learning_rate: f32,
    pub max_rms: f32,
    pub max_tv: f32,
    pub max_code_flip: f32,
    pub max_regression_bits: f32,
    pub min_gain_bits: f32,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            horizon: 8,
            warmup: 128,
            cooldown: 64,
            validation_samples: 32,
            low_steps: 96,
            high_steps: 384,
            learning_rate: 0.03,
            max_rms: 0.12,
            max_tv: 0.45,
            max_code_flip: 0.25,
            max_regression_bits: 0.20,
            min_gain_bits: 0.02,
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
pub struct Budget {
    pub scope: Scope,
    pub steps: usize,
}

pub struct Controller {
    pub count: u64,
    pub fast: f32,
    pub baseline: f32,
    high_streak: u64,
    low_streak: u64,
    last_attempt: Option<u64>,
}
impl Default for Controller {
    fn default() -> Self {
        Self {
            count: 0,
            fast: 0.0,
            baseline: 0.0,
            high_streak: 0,
            low_streak: 0,
            last_attempt: None,
        }
    }
}
impl Controller {
    pub fn observe(&mut self, bits: f32) {
        self.count += 1;
        if self.count == 1 {
            self.fast = bits;
            self.baseline = bits;
        }
        self.fast += 0.15 * (bits - self.fast);
        self.baseline += 0.005 * (bits - self.baseline);
        self.high_streak = if self.fast > 3.0 || self.fast > self.baseline + 1.0 {
            self.high_streak + 1
        } else {
            0
        };
        self.low_streak = if self.fast > 0.75 || self.fast > self.baseline + 0.35 {
            self.low_streak + 1
        } else {
            0
        };
    }
    pub fn decide(&mut self, step: u64, cfg: &Config) -> Option<Budget> {
        if self.count < cfg.warmup {
            return None;
        }
        if let Some(last) = self.last_attempt {
            if step - last < cfg.cooldown {
                return None;
            }
        }
        let budget = if self.last_attempt.is_none() || self.high_streak >= 8 {
            Budget {
                scope: Scope::Full,
                steps: cfg.high_steps,
            }
        } else if self.low_streak >= 16 || self.last_attempt.is_some_and(|last| step - last >= 256)
        {
            Budget {
                scope: Scope::Heads,
                steps: cfg.low_steps,
            }
        } else {
            return None;
        };
        self.last_attempt = Some(step);
        self.high_streak = 0;
        self.low_streak = 0;
        Some(budget)
    }
}

#[derive(Clone)]
pub struct Buffer {
    recent: VecDeque<Example>,
    replay: Vec<Example>,
    pub reference: VecDeque<Example>,
    seen: u64,
    seed: u64,
}
impl Buffer {
    pub fn new(seed: u64) -> Self {
        Self {
            recent: VecDeque::new(),
            replay: Vec::new(),
            reference: VecDeque::new(),
            seen: 0,
            seed,
        }
    }
    pub fn push(&mut self, origin: u64, example: Example) {
        // Assignment is made by origin, before seeing the target. Reference
        // examples NEVER enter either training collection.
        if origin.is_multiple_of(8) {
            self.reference.push_back(example);
            if self.reference.len() > 64 {
                self.reference.pop_front();
            }
            return;
        }
        self.seen += 1;
        self.recent.push_back(example.clone());
        if self.recent.len() > 256 {
            self.recent.pop_front();
        }
        if self.replay.len() < 512 {
            self.replay.push(example);
        } else {
            let mut rng = Rng::new(self.seed ^ self.seen.wrapping_mul(0x9e3779b97f4a7c15));
            let index = rng.next_u64() % self.seen;
            if index < 512 {
                self.replay[index as usize] = example;
            }
        }
    }
    pub fn ready(&self) -> bool {
        self.recent.len() >= 64 && self.reference.len() >= 8
    }
    fn sample(&self, rng: &mut Rng) -> &Example {
        if rng.index(2) == 0 {
            &self.recent[rng.index(self.recent.len())]
        } else {
            &self.replay[rng.index(self.replay.len())]
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Gate {
    pub rms: f32,
    pub tv: f32,
    pub code_flip: f32,
    pub short_regression_bits: f32,
    pub long_regression_bits: f32,
    pub reason: Option<String>,
}

fn compare(base: &Net, candidate: &Net, examples: &VecDeque<Example>) -> (f32, f32) {
    let mut regression = 0.0;
    let mut tv = 0.0;
    for e in examples {
        let p = base.predict(&e.x);
        let q = candidate.predict(&e.x);
        regression += surprise(&q, e.y) - surprise(&p, e.y);
        tv += p.iter().zip(q).map(|(a, b)| (a - b).abs()).sum::<f32>() * 0.5;
    }
    (
        regression / examples.len() as f32,
        tv / examples.len() as f32,
    )
}

pub fn gate(
    base: &Models,
    candidate: &Models,
    short: &Buffer,
    long: &Buffer,
    budget: Budget,
    cfg: &Config,
) -> Gate {
    let mut g = Gate::default();
    if !candidate.valid() {
        g.reason = Some("non-finite or malformed model".into());
        return g;
    }
    if budget.scope == Scope::Heads
        && (!candidate.short.encoder_unchanged(&base.short)
            || !candidate.long.encoder_unchanged(&base.long))
    {
        g.reason = Some("encoder changed during a heads-only update".into());
        return g;
    }
    if short.reference.is_empty() || long.reference.is_empty() {
        g.reason = Some("insufficient reference data".into());
        return g;
    }
    g.rms = candidate
        .short
        .rms_change(&base.short)
        .max(candidate.long.rms_change(&base.long));
    let (sr, stv) = compare(&base.short, &candidate.short, &short.reference);
    let (lr, ltv) = compare(&base.long, &candidate.long, &long.reference);
    g.short_regression_bits = sr;
    g.long_regression_bits = lr;
    g.tv = stv.max(ltv);
    g.code_flip = long
        .reference
        .iter()
        .filter(|e| base.long.token(&e.x) != candidate.long.token(&e.x))
        .count() as f32
        / long.reference.len() as f32;
    g.reason = if g.rms > cfg.max_rms {
        Some("parameter RMS limit")
    } else if g.tv > cfg.max_tv {
        Some("reference output distribution moved too far")
    } else if g.code_flip > cfg.max_code_flip {
        Some("too many reference codes changed meaning")
    } else if sr > cfg.max_regression_bits || lr > cfg.max_regression_bits {
        Some("reference predictive regression")
    } else {
        None
    }
    .map(str::to_owned);
    g
}

pub struct Trained {
    pub model: Models,
    pub budget: Budget,
    pub completed_steps: usize,
    pub retained_steps: usize,
    pub stop_reason: Option<String>,
    pub elapsed_ms: u64,
    pub gate: Gate,
}

/// Each step updates each network once. A real-sensor job also has a wall-time
/// cap; the deterministic simulator uses only the exact update-count budget.
pub fn train(
    base: Models,
    short: Buffer,
    long: Buffer,
    budget: Budget,
    cfg: Config,
    max_ms: Option<u64>,
    seed: u64,
) -> Trained {
    let start = Instant::now();
    let mut model = base.clone();
    let mut last_safe = base.clone();
    let mut rng = Rng::new(seed);
    let mut completed_steps: usize = 0;
    let mut retained_steps = 0;
    let mut stop_reason = None;
    for _ in 0..budget.steps {
        if max_ms.is_some_and(|ms| start.elapsed().as_millis() >= u128::from(ms)) {
            break;
        }
        model
            .short
            .train_one(short.sample(&mut rng), cfg.learning_rate, budget.scope);
        model
            .long
            .train_one(long.sample(&mut rng), cfg.learning_rate, budget.scope);
        completed_steps += 1;
        if completed_steps.is_multiple_of(16) || completed_steps == budget.steps {
            let check = gate(&base, &model, &short, &long, budget, &cfg);
            if check.reason.is_some() {
                stop_reason = check.reason;
                model = last_safe.clone();
                break;
            }
            last_safe = model.clone();
            retained_steps = completed_steps;
        }
    }
    // Also check a partial chunk when the wall-time cap stopped the loop.
    let final_check = gate(&base, &model, &short, &long, budget, &cfg);
    if final_check.reason.is_some() {
        stop_reason = final_check.reason;
        model = last_safe;
    } else if stop_reason.is_none() {
        retained_steps = completed_steps;
    }
    let gate = gate(&base, &model, &short, &long, budget, &cfg);
    Trained {
        model,
        budget,
        completed_steps,
        retained_steps,
        stop_reason,
        elapsed_ms: start.elapsed().as_millis() as u64,
        gate,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::INPUTS;
    #[test]
    fn one_spike_does_not_trigger_training_and_cooldown_is_enforced() {
        let cfg = Config::default();
        let mut c = Controller::default();
        for _ in 0..128 {
            c.observe(0.2);
        }
        assert!(c.decide(128, &cfg).is_some()); // initial fitting
        for _ in 0..100 {
            c.observe(0.2);
        }
        c.observe(12.0);
        assert!(c.decide(229, &cfg).is_none());
        for _ in 0..32 {
            c.observe(5.0);
        }
        assert_eq!(c.decide(261, &cfg).unwrap().scope, Scope::Full);
        assert!(c.decide(262, &cfg).is_none());
    }
    #[test]
    fn holdouts_are_disjoint_and_memory_is_bounded() {
        let mut b = Buffer::new(1);
        for n in 0..10_000 {
            b.push(
                n,
                Example {
                    x: vec![n as f32; INPUTS],
                    y: 0,
                },
            );
        }
        assert_eq!(b.recent.len(), 256);
        assert_eq!(b.replay.len(), 512);
        assert_eq!(b.reference.len(), 64);
        assert!(b
            .recent
            .iter()
            .chain(&b.replay)
            .all(|e| !(e.x[0] as u64).is_multiple_of(8)));
        assert!(b
            .reference
            .iter()
            .all(|e| (e.x[0] as u64).is_multiple_of(8)));
    }
}
