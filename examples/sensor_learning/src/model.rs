//! Small two-layer networks with explicit, clipped SGD.
//!
//! Layer two's four hidden signs are its actual predictive bottleneck.
//! Backpropagation through sign uses the straight-through approximation;
//! it is not the mathematical derivative of a discrete token.
use serde::{Deserialize, Serialize};

pub const VOCAB: usize = 32;
pub const INPUTS: usize = 40;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Scope {
    Heads,
    Full,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Example {
    pub x: Vec<f32>,
    pub y: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Net {
    hidden: usize,
    discrete: bool,
    w1: Vec<f32>,
    b1: Vec<f32>,
    w2: Vec<f32>,
    b2: Vec<f32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Models {
    pub version: u64,
    pub short: Net,
    pub long: Net,
}

pub struct Rng(u64);
impl Rng {
    pub fn new(seed: u64) -> Self {
        Self(seed.max(1))
    }
    pub fn next_u64(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    pub fn index(&mut self, n: usize) -> usize {
        (self.next_u64() % n as u64) as usize
    }
    pub fn symmetric(&mut self) -> f32 {
        (self.next_u64() >> 40) as f32 / 8_388_608.0 - 1.0
    }
}

pub fn surprise(p: &[f32], target: usize) -> f32 {
    -p[target].max(1e-9).log2()
}

impl Net {
    pub fn new(hidden: usize, discrete: bool, seed: u64) -> Self {
        let mut rng = Rng::new(seed);
        Self {
            hidden,
            discrete,
            w1: (0..hidden * INPUTS)
                .map(|_| rng.symmetric() * 0.2)
                .collect(),
            b1: vec![0.0; hidden],
            w2: (0..VOCAB * hidden)
                .map(|_| rng.symmetric() * 0.05)
                .collect(),
            b2: vec![0.0; VOCAB],
        }
    }

    fn forward(&self, x: &[f32]) -> (Vec<f32>, Vec<f32>, Vec<f32>) {
        assert_eq!(x.len(), INPUTS);
        let hidden: Vec<f32> = (0..self.hidden)
            .map(|j| {
                (self.b1[j]
                    + self.w1[j * INPUTS..(j + 1) * INPUTS]
                        .iter()
                        .zip(x)
                        .map(|(w, v)| w * v)
                        .sum::<f32>())
                .tanh()
            })
            .collect();
        let code: Vec<f32> = hidden
            .iter()
            .map(|&h| {
                if self.discrete {
                    if h >= 0.0 {
                        1.0
                    } else {
                        -1.0
                    }
                } else {
                    h
                }
            })
            .collect();
        let mut p: Vec<f32> = (0..VOCAB)
            .map(|k| {
                self.b2[k]
                    + self.w2[k * self.hidden..(k + 1) * self.hidden]
                        .iter()
                        .zip(&code)
                        .map(|(w, h)| w * h)
                        .sum::<f32>()
            })
            .collect();
        let max = p.iter().copied().fold(f32::NEG_INFINITY, f32::max);
        for v in &mut p {
            *v = (*v - max).exp();
        }
        let sum: f32 = p.iter().sum();
        for v in &mut p {
            *v /= sum;
        }
        (hidden, code, p)
    }

    pub fn predict(&self, x: &[f32]) -> Vec<f32> {
        self.forward(x).2
    }

    pub fn token(&self, x: &[f32]) -> u8 {
        assert!(self.discrete && self.hidden == 4);
        self.forward(x)
            .1
            .iter()
            .enumerate()
            .fold(0u8, |code, (i, h)| code | (u8::from(*h > 0.0) << i))
    }

    pub fn train_one(&mut self, e: &Example, rate: f32, scope: Scope) {
        let (hidden, code, mut delta) = self.forward(&e.x);
        delta[e.y] -= 1.0; // softmax cross entropy, in natural-log units
        let dh: Vec<f32> = (0..self.hidden)
            .map(|j| {
                delta
                    .iter()
                    .enumerate()
                    .map(|(k, d)| d * self.w2[k * self.hidden + j])
                    .sum::<f32>()
                    * (1.0 - hidden[j] * hidden[j])
            })
            .collect();
        let mut norm2 = delta.iter().map(|d| d * d).sum::<f32>()
            * (1.0 + code.iter().map(|h| h * h).sum::<f32>());
        if scope == Scope::Full {
            norm2 += dh.iter().map(|h| h * h).sum::<f32>()
                * (1.0 + e.x.iter().map(|x| x * x).sum::<f32>());
        }
        let rate = rate / norm2.sqrt().max(1.0); // global gradient norm <= 1
        for (k, d) in delta.iter().enumerate() {
            self.b2[k] -= rate * d;
            for (j, h) in code.iter().enumerate() {
                self.w2[k * self.hidden + j] -= rate * d * h;
            }
        }
        if scope == Scope::Full {
            for (j, d) in dh.iter().enumerate() {
                self.b1[j] -= rate * d;
                for (i, x) in e.x.iter().enumerate() {
                    self.w1[j * INPUTS + i] -= rate * d * x;
                }
            }
        }
    }

    pub fn parameters(&self) -> impl Iterator<Item = &f32> {
        self.w1
            .iter()
            .chain(&self.b1)
            .chain(&self.w2)
            .chain(&self.b2)
    }
    pub fn encoder_unchanged(&self, base: &Self) -> bool {
        self.w1 == base.w1 && self.b1 == base.b1
    }
    pub fn rms_change(&self, base: &Self) -> f32 {
        let sum: f32 = self
            .parameters()
            .zip(base.parameters())
            .map(|(a, b)| (a - b).powi(2))
            .sum();
        (sum / self.parameters().count() as f32).sqrt()
    }
    pub fn valid(&self, hidden: usize, discrete: bool) -> bool {
        self.hidden == hidden
            && self.discrete == discrete
            && self.w1.len() == hidden * INPUTS
            && self.b1.len() == hidden
            && self.w2.len() == VOCAB * hidden
            && self.b2.len() == VOCAB
            && self
                .parameters()
                .all(|v| v.is_finite() && v.abs() < 1_000.0)
    }
}

impl Models {
    pub fn new() -> Self {
        Self {
            version: 0,
            short: Net::new(16, false, 7),
            long: Net::new(4, true, 17),
        }
    }
    pub fn valid(&self) -> bool {
        self.short.valid(16, false) && self.long.valid(4, true)
    }
    pub fn parameter_count(&self) -> usize {
        self.short.parameters().count() + self.long.parameters().count()
    }
}

impl Default for Models {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn surprise_is_probability_of_the_observed_target() {
        assert!((surprise(&[0.5, 0.01], 0) - 1.0).abs() < 1e-6);
        assert!((surprise(&[0.5, 0.01], 1) - 6.643856).abs() < 1e-5);
    }
    #[test]
    fn head_training_reduces_loss_without_changing_encoder() {
        let mut net = Net::new(16, false, 7);
        let base = net.clone();
        let mut x = vec![0.0; INPUTS];
        x[12] = 1.0;
        let example = Example { x, y: 12 };
        let before = surprise(&net.predict(&example.x), 12);
        for _ in 0..200 {
            net.train_one(&example, 0.05, Scope::Heads);
        }
        assert!(surprise(&net.predict(&example.x), 12) < before - 2.0);
        assert!(net.encoder_unchanged(&base));
    }
    #[test]
    fn ordinary_backprop_matches_a_finite_difference() {
        let mut net = Net::new(16, false, 9);
        // Keep the gradient below the clipping threshold for this derivative test.
        for w in &mut net.w1 {
            *w *= 0.1;
        }
        let mut x = vec![0.0; INPUTS];
        x[0] = 1.0;
        let e = Example { x, y: 3 };
        let eps = 0.001;
        let old = net.w1[0];
        net.w1[0] = old + eps;
        let plus = -net.predict(&e.x)[e.y].ln();
        net.w1[0] = old - eps;
        let minus = -net.predict(&e.x)[e.y].ln();
        net.w1[0] = old;
        let numerical = (plus - minus) / (2.0 * eps);
        // This small example's unclipped gradient norm is below one.
        net.train_one(&e, 0.01, Scope::Full);
        let analytic = (old - net.w1[0]) / 0.01;
        assert!(
            (analytic - numerical).abs() < 0.001,
            "{analytic} vs {numerical}"
        );
    }
    #[test]
    fn discrete_bottleneck_really_controls_the_prediction() {
        let net = Net::new(4, true, 17);
        let a = vec![1.0; INPUTS];
        let b = vec![2.0; INPUTS];
        // With zero biases, positive rescaling preserves all four hidden signs.
        assert_eq!(net.token(&a), net.token(&b));
        assert_eq!(net.predict(&a), net.predict(&b));
        assert_eq!(Models::new().parameter_count(), 1524);
    }
}
