use crate::{
    learning::{self, Budget, Buffer, Config, Controller, Trained},
    model::{surprise, Example, Models, INPUTS, VOCAB},
    sensor::{tokenize, Reading},
};
use serde::Serialize;
use std::collections::VecDeque;

#[derive(Clone, Debug, Serialize)]
pub struct Forecast {
    pub origin: u64,
    pub target: u64,
    pub layer: usize,
    pub version: u64,
    pub probabilities: Vec<f32>,
    pub candidate_probabilities: Option<Vec<f32>>,
    pub attempt: Option<u64>,
    pub code: Option<u8>,
}
#[derive(Clone, Debug, Serialize)]
pub struct Score {
    pub origin: u64,
    pub target: u64,
    pub layer: usize,
    pub actual: usize,
    pub bits: f32,
    pub candidate_bits: Option<f32>,
    pub attempt: Option<u64>,
}
#[derive(Clone, Debug, Serialize)]
pub struct Event {
    pub step: u64,
    pub kind: String,
    pub detail: serde_json::Value,
}

pub struct Batch {
    pub step: u64,
    pub reading: Reading,
    pub token: Option<usize>,
    pub scores: Vec<Score>,
    pub forecasts: Vec<Forecast>,
    pub checkpoint: Option<Models>,
}

struct Pending {
    forecast: Forecast,
    x: Vec<f32>,
    candidate: Option<(u64, Vec<f32>)>,
}
struct Shadow {
    trained: Trained,
    id: u64,
    started: u64,
    count: [usize; 2],
    live_bits: [f32; 2],
    candidate_bits: [f32; 2],
    tv: [f32; 2],
    forecasts: [usize; 2],
    code_flips: usize,
}

pub struct Job {
    base: Models,
    buffers: [Buffer; 2],
    budget: Budget,
    cfg: Config,
    seed: u64,
}
impl Job {
    pub fn run(self, max_ms: Option<u64>) -> Trained {
        let [short, long] = self.buffers;
        learning::train(
            self.base,
            short,
            long,
            self.budget,
            self.cfg,
            max_ms,
            self.seed,
        )
    }
}

#[derive(Default, Serialize)]
pub struct Stats {
    pub valid: u64,
    pub invalid: u64,
    pub attempts: u64,
    pub promotions: u64,
    pub rejections: u64,
    pub scored: [u64; 2],
    pub total_bits: [f64; 2],
    pub first_short_bits: Vec<f32>,
    pub recent_short_bits: VecDeque<f32>,
}

pub struct Engine {
    pub cfg: Config,
    pub model: Models,
    pub step: u64,
    pub stats: Stats,
    controller: Controller,
    buffers: [Buffer; 2],
    pending: VecDeque<Pending>,
    previous: Option<(i32, u64)>,
    latest_surprise: f32,
    busy: bool,
    shadow: Option<Shadow>,
    events: Vec<Event>,
}
impl Engine {
    pub fn new(cfg: Config, model: Models) -> Self {
        assert!(cfg.horizon > 1 && cfg.validation_samples > 0 && model.valid());
        Self {
            cfg,
            model,
            step: 0,
            stats: Stats::default(),
            controller: Controller::default(),
            buffers: [Buffer::new(71), Buffer::new(73)],
            pending: VecDeque::new(),
            previous: None,
            latest_surprise: 0.0,
            busy: false,
            shadow: None,
            events: Vec::new(),
        }
    }
    fn event(&mut self, kind: &str, detail: serde_json::Value) {
        self.events.push(Event {
            step: self.step,
            kind: kind.into(),
            detail,
        });
    }
    pub fn drain_events(&mut self) -> Vec<Event> {
        std::mem::take(&mut self.events)
    }

    pub fn training_job(&mut self) -> Option<Job> {
        if self.busy
            || self.shadow.is_some()
            || self.previous.is_none()
            || !self.buffers.iter().all(Buffer::ready)
        {
            return None;
        }
        let budget = self.controller.decide(self.step, &self.cfg)?;
        self.busy = true;
        self.stats.attempts += 1;
        self.event("training_started", serde_json::json!({
            "attempt": self.stats.attempts, "base_version": self.model.version,
            "budget": budget, "fast_bits": self.controller.fast, "baseline_bits": self.controller.baseline,
        }));
        Some(Job {
            base: self.model.clone(),
            buffers: self.buffers.clone(),
            budget,
            cfg: self.cfg.clone(),
            seed: 1009 + self.stats.attempts,
        })
    }

    pub fn accept_trained(&mut self, result: Trained) {
        assert!(self.busy && self.shadow.is_none());
        self.busy = false;
        let id = self.stats.attempts;
        self.event(
            "training_finished",
            serde_json::json!({
                "attempt": id, "completed_steps": result.completed_steps,
                "retained_steps": result.retained_steps, "stop_reason": result.stop_reason,
                "elapsed_ms": result.elapsed_ms, "budget": result.budget, "gate": result.gate,
            }),
        );
        if result.gate.reason.is_some() {
            self.stats.rejections += 1;
            self.event(
                "rejected_reference",
                serde_json::json!({"attempt": id, "gate": result.gate}),
            );
            return;
        }
        self.shadow = Some(Shadow {
            trained: result,
            id,
            started: self.step,
            count: [0; 2],
            live_bits: [0.0; 2],
            candidate_bits: [0.0; 2],
            tv: [0.0; 2],
            forecasts: [0; 2],
            code_flips: 0,
        });
        self.event(
            "validation_started",
            serde_json::json!({"attempt": id, "after_step": self.step}),
        );
    }

    pub fn observe(&mut self, mut reading: Reading) -> Batch {
        self.step += 1;
        let token = reading.raw.and_then(tokenize);
        if token.is_none() && reading.problem.is_none() {
            reading.problem = Some("temperature outside the fixed tokenizer range".into());
        }
        // Monotonic clock, load, and measured training activity are part of the
        // observation contract; malformed context must not enter the network.
        if !reading.load.is_finite()
            || !(0.0..=1.0).contains(&reading.load)
            || !reading.training_duty.is_finite()
            || !(0.0..=1.0).contains(&reading.training_duty)
            || self
                .previous
                .is_some_and(|(_, time)| reading.millis <= time)
        {
            reading.problem = Some("invalid context or non-increasing observation time".into());
        }
        let token = if reading.problem.is_some() {
            None
        } else {
            token
        };
        let mut batch = Batch {
            step: self.step,
            reading,
            token,
            scores: Vec::new(),
            forecasts: Vec::new(),
            checkpoint: None,
        };
        let Some(actual) = token else {
            self.stats.invalid += 1;
            self.pending.clear();
            self.previous = None;
            self.expire_shadow();
            return batch;
        };
        self.stats.valid += 1;
        // Predictions were issued on earlier ticks, with frozen probabilities.
        let mut waiting = VecDeque::new();
        while let Some(p) = self.pending.pop_front() {
            if p.forecast.target > self.step {
                waiting.push_back(p);
                continue;
            }
            assert_eq!(p.forecast.target, self.step);
            let layer = p.forecast.layer;
            let bits = surprise(&p.forecast.probabilities, actual);
            let candidate_bits = p.candidate.as_ref().map(|(_, q)| surprise(q, actual));
            if let (Some(s), Some((id, _))) = (&mut self.shadow, &p.candidate) {
                if s.id == *id {
                    s.count[layer] += 1;
                    s.live_bits[layer] += bits;
                    s.candidate_bits[layer] += candidate_bits.unwrap();
                }
            }
            self.stats.scored[layer] += 1;
            self.stats.total_bits[layer] += f64::from(bits);
            if layer == 0 {
                self.latest_surprise = bits;
                self.controller.observe(bits);
                if self.stats.first_short_bits.len() < 256 {
                    self.stats.first_short_bits.push(bits);
                }
                self.stats.recent_short_bits.push_back(bits);
                if self.stats.recent_short_bits.len() > 256 {
                    self.stats.recent_short_bits.pop_front();
                }
            }
            self.buffers[layer].push(p.forecast.origin, Example { x: p.x, y: actual });
            batch.scores.push(Score {
                origin: p.forecast.origin,
                target: self.step,
                layer,
                actual,
                bits,
                candidate_bits,
                attempt: p.candidate.map(|(id, _)| id),
            });
        }
        self.pending = waiting;
        batch.checkpoint = self.finish_validation();
        self.expire_shadow();

        let raw = batch.reading.raw.unwrap();
        let (delta, dt) = self
            .previous
            .map(|(previous, time)| {
                (
                    (raw - previous) as f32 / 16_000.0,
                    (batch.reading.millis - time) as f32 / 1_000.0,
                )
            })
            .unwrap_or((0.0, 1.0));
        let mut x = vec![0.0; INPUTS];
        x[actual] = 1.0;
        x[32] = raw as f32 / 64_000.0 - 1.0;
        x[33] = delta.clamp(-1.0, 1.0);
        x[34] = batch.reading.load * 2.0 - 1.0;
        x[35] = dt.min(10.0) / 5.0 - 1.0;
        x[36] = batch.reading.training_duty * 2.0 - 1.0;
        x[37] = (self.latest_surprise / 8.0).min(1.0);
        x[38] = 0.0; // Filled with the first predictor's next-token prediction for layer two.
        x[39] = 1.0;
        let short_p = self.model.short.predict(&x);
        let predicted = short_p
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.total_cmp(b.1))
            .unwrap()
            .0;
        self.issue(0, x.clone(), short_p, None, &mut batch);
        x[38] = predicted as f32 / (VOCAB - 1) as f32 * 2.0 - 1.0;
        let code = self.model.long.token(&x);
        let long_p = self.model.long.predict(&x);
        self.issue(1, x, long_p, Some(code), &mut batch);
        self.previous = Some((raw, batch.reading.millis));
        batch
    }

    fn issue(
        &mut self,
        layer: usize,
        x: Vec<f32>,
        p: Vec<f32>,
        code: Option<u8>,
        batch: &mut Batch,
    ) {
        let candidate = self.shadow.as_mut().map(|s| {
            // The candidate consumes the SAME recorded feature vector as the live
            // model. This evaluates the local layer change, not a recursive rollout.
            let q = if layer == 0 {
                s.trained.model.short.predict(&x)
            } else {
                s.trained.model.long.predict(&x)
            };
            s.tv[layer] += p.iter().zip(&q).map(|(a, b)| (a - b).abs()).sum::<f32>() * 0.5;
            s.forecasts[layer] += 1;
            if layer == 1 && Some(s.trained.model.long.token(&x)) != code {
                s.code_flips += 1;
            }
            (s.id, q)
        });
        let forecast = Forecast {
            origin: self.step,
            target: self.step + if layer == 0 { 1 } else { self.cfg.horizon },
            layer,
            version: self.model.version,
            probabilities: p,
            candidate_probabilities: candidate.as_ref().map(|(_, p)| p.clone()),
            attempt: candidate.as_ref().map(|(id, _)| *id),
            code,
        };
        batch.forecasts.push(forecast.clone());
        self.pending.push_back(Pending {
            forecast,
            x,
            candidate,
        });
    }

    fn finish_validation(&mut self) -> Option<Models> {
        let ready = self
            .shadow
            .as_ref()
            .is_some_and(|s| s.count.iter().all(|n| *n >= self.cfg.validation_samples));
        if !ready {
            return None;
        }
        let s = self.shadow.take().unwrap();
        let gain = [0, 1].map(|i| (s.live_bits[i] - s.candidate_bits[i]) / s.count[i] as f32);
        let tv = [0, 1].map(|i| s.tv[i] / s.forecasts[i] as f32);
        let code_flip = s.code_flips as f32 / s.forecasts[1] as f32;
        let promote = gain.iter().all(|v| *v >= -self.cfg.max_regression_bits)
            && (gain[0] + gain[1]) * 0.5 >= self.cfg.min_gain_bits
            && tv.iter().all(|v| *v <= self.cfg.max_tv)
            && code_flip <= self.cfg.max_code_flip;
        let detail = serde_json::json!({
            "attempt": s.id, "base_version": self.model.version, "gain_bits": gain,
            "count": s.count, "tv": tv, "code_flip": code_flip,
        });
        if promote {
            let version = self.model.version + 1;
            self.model = s.trained.model;
            self.model.version = version;
            self.stats.promotions += 1;
            self.event("promoted", detail);
            Some(self.model.clone())
        } else {
            self.stats.rejections += 1;
            self.event("rejected_future", detail);
            None
        }
    }

    fn expire_shadow(&mut self) {
        if self
            .shadow
            .as_ref()
            .is_some_and(|s| self.step - s.started > 256)
        {
            let id = self.shadow.take().unwrap().id;
            self.stats.rejections += 1;
            self.event("rejected_timeout", serde_json::json!({"attempt": id}));
        }
    }
    pub fn shutdown(&mut self) {
        if self.busy || self.shadow.is_some() {
            self.event(
                "unfinished_candidate",
                serde_json::json!({"attempt": self.stats.attempts}),
            );
        }
        self.event(
            "stopped",
            serde_json::json!({"version": self.model.version, "steps": self.step}),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn reading(t: u64, raw: Option<i32>) -> Reading {
        Reading {
            millis: t * 1_000,
            raw,
            load: 0.2,
            training_duty: 0.0,
            problem: None,
        }
    }
    #[test]
    fn scores_the_original_forecast_and_never_the_current_observation() {
        let mut e = Engine::new(Config::default(), Models::new());
        let first = e.observe(reading(1, Some(40_000)));
        assert!(first.scores.is_empty());
        let expected = surprise(&first.forecasts[0].probabilities, 11);
        // Even an intervening model update cannot rewrite the saved forecast.
        let example = Example {
            x: vec![1.0; INPUTS],
            y: 11,
        };
        for _ in 0..50 {
            e.model
                .short
                .train_one(&example, 0.03, crate::model::Scope::Full);
        }
        let second = e.observe(reading(2, Some(44_000)));
        assert_eq!(second.scores[0].bits, expected);
        assert_eq!(second.scores[0].origin, 1);
        assert_eq!(second.scores[0].target, 2);
    }
    #[test]
    fn gaps_cancel_pending_targets_instead_of_bridging_missing_time() {
        let mut e = Engine::new(Config::default(), Models::new());
        e.observe(reading(1, Some(40_000)));
        assert!(e.observe(reading(2, None)).scores.is_empty());
        assert!(e.observe(reading(3, Some(40_000))).scores.is_empty());
        assert_eq!(e.observe(reading(4, Some(40_000))).scores.len(), 1);
    }
}
