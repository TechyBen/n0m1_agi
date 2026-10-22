use crate::{model::Rng, Result};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
};

pub const CODEC: &str = "temp-millic-le-i32-0-to-128000-32-bins-v1";

/// The kernel has already decoded the device register. These bytes are our
/// canonical serialization, not a claim about the physical sensor's wire format.
pub fn bytes(raw: i32) -> [u8; 4] {
    raw.to_le_bytes()
}

pub fn tokenize(raw: i32) -> Option<usize> {
    (0..128_000)
        .contains(&raw)
        .then_some((raw as usize) / 4_000)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Reading {
    pub millis: u64,
    pub raw: Option<i32>,
    pub load: f32,
    pub training_duty: f32,
    pub problem: Option<String>,
}

pub struct Simulator {
    tick: u64,
    temp: f32,
    rng: Rng,
}
impl Simulator {
    pub fn new(seed: u64) -> Self {
        Self {
            tick: 0,
            temp: 40_000.0,
            rng: Rng::new(seed),
        }
    }
    pub fn read(&mut self) -> Reading {
        self.tick += 1;
        // A repeatable load cycle plus a later change in ambient temperature.
        let load = if (self.tick / 160).is_multiple_of(2) {
            0.12
        } else {
            0.85
        };
        let ambient = if self.tick < 1_200 {
            34_000.0
        } else {
            42_000.0
        };
        self.temp += 0.08 * (ambient + 38_000.0 * load - self.temp) + self.rng.symmetric() * 120.0;
        let missing = self.tick.is_multiple_of(997);
        Reading {
            millis: self.tick * 1_000,
            raw: (!missing).then_some(self.temp.round() as i32),
            load,
            training_duty: 0.0,
            problem: missing.then(|| "simulated missing sample".into()),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Sensor {
    pub path: PathBuf,
    pub driver: String,
    pub label: String,
}

pub fn discover(root: &Path) -> Result<Vec<Sensor>> {
    let mut sensors = Vec::new();
    for entry in fs::read_dir(root)? {
        let dir = entry?.path();
        let driver = fs::read_to_string(dir.join("name"))
            .unwrap_or_default()
            .trim()
            .to_owned();
        for item in fs::read_dir(&dir).into_iter().flatten().flatten() {
            let name = item.file_name().to_string_lossy().into_owned();
            let Some(index) = name
                .strip_prefix("temp")
                .and_then(|s| s.strip_suffix("_input"))
            else {
                continue;
            };
            if index.is_empty() || !index.bytes().all(|b| b.is_ascii_digit()) {
                continue;
            }
            let label = fs::read_to_string(dir.join(format!("temp{index}_label")))
                .unwrap_or_default()
                .trim()
                .to_owned();
            sensors.push(Sensor {
                path: item.path(),
                driver: driver.clone(),
                label,
            });
        }
    }
    sensors.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(sensors)
}

pub fn select_cpu(sensors: &[Sensor]) -> Result<Sensor> {
    for label in ["Tdie", "Tctl"] {
        let matches: Vec<_> = sensors
            .iter()
            .filter(|s| s.driver == "k10temp" && s.label == label)
            .collect();
        if matches.len() == 1 {
            return Ok(matches[0].clone());
        }
        if matches.len() > 1 {
            return Err(
                format!("multiple k10temp {label} sensors; select --sensor explicitly").into(),
            );
        }
    }
    Err("no unambiguous k10temp Tdie/Tctl sensor; run --list-sensors and select --sensor".into())
}

/// Process-wide CPU load from deltas of /proc/stat (guest columns excluded).
#[derive(Default)]
pub struct CpuLoad {
    previous: Option<(u64, u64)>,
}
impl CpuLoad {
    pub fn read(&mut self, path: &Path) -> Result<f32> {
        let text = fs::read_to_string(path)?;
        let mut line = text
            .lines()
            .next()
            .ok_or("empty /proc/stat")?
            .split_whitespace();
        if line.next() != Some("cpu") {
            return Err("missing aggregate cpu line".into());
        }
        let fields: Vec<u64> = line
            .take(8)
            .map(str::parse)
            .collect::<std::result::Result<_, _>>()?;
        if fields.len() < 4 {
            return Err("short aggregate cpu line".into());
        }
        let total: u64 = fields.iter().sum();
        let idle = fields[3] + fields.get(4).copied().unwrap_or(0);
        let load = self
            .previous
            .map(|(old_total, old_idle)| {
                let elapsed = total.saturating_sub(old_total);
                if elapsed == 0 {
                    0.0
                } else {
                    1.0 - idle.saturating_sub(old_idle) as f32 / elapsed as f32
                }
            })
            .unwrap_or(0.0)
            .clamp(0.0, 1.0);
        self.previous = Some((total, idle));
        Ok(load)
    }
}

pub fn read_hwmon(sensor: &Sensor, millis: u64, load: f32, training_duty: f32) -> Reading {
    match fs::read_to_string(&sensor.path)
        .map_err(|e| e.to_string())
        .and_then(|s| s.trim().parse::<i32>().map_err(|e| e.to_string()))
    {
        Ok(raw) => Reading {
            millis,
            raw: Some(raw),
            load,
            training_duty,
            problem: None,
        },
        Err(problem) => Reading {
            millis,
            raw: None,
            load,
            training_duty,
            problem: Some(problem),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn codec_is_explicit_and_does_not_clamp_bad_data() {
        assert_eq!(i32::from_le_bytes(bytes(52_000)), 52_000);
        assert_eq!(tokenize(52_000), Some(13));
        assert_eq!(tokenize(127_999), Some(31));
        assert_eq!(tokenize(-1), None);
        assert_eq!(tokenize(128_000), None);
    }
    #[test]
    fn selects_by_driver_and_label_not_hwmon_number() {
        let sensors = vec![
            Sensor {
                path: "hwmon0/temp1_input".into(),
                driver: "acpitz".into(),
                label: "".into(),
            },
            Sensor {
                path: "hwmon5/temp1_input".into(),
                driver: "k10temp".into(),
                label: "Tctl".into(),
            },
            Sensor {
                path: "hwmon5/temp2_input".into(),
                driver: "k10temp".into(),
                label: "Tdie".into(),
            },
        ];
        assert_eq!(select_cpu(&sensors).unwrap().label, "Tdie");
        let mut ambiguous = sensors.clone();
        ambiguous.push(sensors[2].clone());
        assert!(select_cpu(&ambiguous).is_err());
    }
}
