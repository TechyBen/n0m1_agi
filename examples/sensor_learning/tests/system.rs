use n0m1_sensorlab::{
    engine::Engine,
    learning::{self, Budget, Buffer, Config},
    model::{surprise, Example, Models, Scope, INPUTS},
    sensor::{self, CpuLoad, Simulator},
    store::Store,
};
use rusqlite::Connection;
use std::{
    collections::HashMap,
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT: AtomicU64 = AtomicU64::new(0);
struct TempDir(PathBuf);
impl TempDir {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "n0m1-sensor-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
}
impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn simulator_learns_and_promotes_only_after_fresh_paired_targets() {
    let cfg = Config::default();
    let mut learner = Engine::new(cfg.clone(), Models::new());
    let mut frozen = Engine::new(cfg.clone(), Models::new());
    let mut sim = Simulator::new(7);
    let mut started = HashMap::new();
    for _ in 0..3000 {
        let reading = sim.read();
        learner.observe(reading.clone());
        frozen.observe(reading);
        if let Some(job) = learner.training_job() {
            learner.accept_trained(job.run(None));
        }
        for event in learner.drain_events() {
            let attempt = event.detail["attempt"].as_u64().unwrap();
            if event.kind == "validation_started" {
                started.insert(attempt, event.step);
            }
            if event.kind == "promoted" {
                assert!(
                    event.step >= started[&attempt] + cfg.horizon + cfg.validation_samples as u64
                );
                for count in event.detail["count"].as_array().unwrap() {
                    assert!(count.as_u64().unwrap() >= cfg.validation_samples as u64);
                }
                let gain: Vec<_> = event.detail["gain_bits"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|x| x.as_f64().unwrap())
                    .collect();
                assert!(gain
                    .iter()
                    .all(|g| *g >= -f64::from(cfg.max_regression_bits)));
                assert!((gain[0] + gain[1]) * 0.5 >= f64::from(cfg.min_gain_bits));
                assert!(
                    event.detail["code_flip"].as_f64().unwrap() <= f64::from(cfg.max_code_flip)
                );
            }
        }
    }
    assert_eq!(learner.stats.invalid, 3);
    assert!(learner.stats.promotions > 0);
    assert!(learner.stats.rejections > 0);
    assert!(learner.stats.total_bits[0] < frozen.stats.total_bits[0] * 0.9);
    assert!(learner.stats.total_bits[1] < frozen.stats.total_bits[1]);
}

fn training_buffers() -> [Buffer; 2] {
    let mut buffers = [Buffer::new(1), Buffer::new(2)];
    let mut x = vec![0.0; INPUTS];
    x[9] = 1.0;
    for n in 0..160 {
        for b in &mut buffers {
            b.push(n, Example { x: x.clone(), y: 9 });
        }
    }
    buffers
}

#[test]
fn oversized_training_is_bounded_without_relaxing_the_gate() {
    let cfg = Config::default();
    let base = Models::new();
    let [short, long] = training_buffers();
    let budget = Budget {
        scope: Scope::Full,
        steps: cfg.high_steps,
    };
    let trained = learning::train(
        base.clone(),
        short.clone(),
        long.clone(),
        budget,
        cfg.clone(),
        None,
        1,
    );
    assert!(trained.stop_reason.is_some());
    assert!(trained.retained_steps > 0 && trained.retained_steps < trained.completed_steps);
    assert!(trained.gate.reason.is_none());
    assert!(trained.gate.rms <= cfg.max_rms && trained.gate.tv <= cfg.max_tv);
    let mut too_large = base.clone();
    let mut x = vec![0.0; INPUTS];
    x[9] = 1.0;
    for _ in 0..1000 {
        too_large
            .short
            .train_one(&Example { x: x.clone(), y: 9 }, 0.03, Scope::Full);
    }
    assert!(
        learning::gate(&base, &too_large, &short, &long, budget, &cfg)
            .reason
            .is_some()
    );
    let heads = Budget {
        scope: Scope::Heads,
        steps: 96,
    };
    assert!(
        learning::gate(&base, &too_large, &short, &long, heads, &cfg)
            .reason
            .unwrap()
            .contains("encoder")
    );
}

#[test]
fn sqlite_records_forecasts_before_scores_and_resumes_only_compatible_weights() {
    let tmp = TempDir::new();
    let path = tmp.0.join("observations.db");
    let cfg = Config::default();
    let (mut store, model) = Store::open(&path, "simulation:7", &cfg, false).unwrap();
    let mut engine = Engine::new(cfg.clone(), model);
    let mut sim = Simulator::new(7);
    let connection = Connection::open(&path).unwrap();
    for _ in 0..450 {
        let batch = engine.observe(sim.read());
        store.record(&batch).unwrap();
        let unscored: i64 = connection
            .query_row(
                "SELECT count(*) FROM sensor_predictions WHERE origin=?1 AND actual IS NULL",
                [batch.step],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(unscored, 2);
        if let Some(job) = engine.training_job() {
            engine.accept_trained(job.run(None));
        }
        store.events(&engine.drain_events()).unwrap();
    }
    store.finish().unwrap();
    let mut rows = connection
        .prepare(
            "SELECT probabilities,candidate_probabilities,actual,surprise_bits,candidate_bits
        FROM sensor_predictions WHERE actual IS NOT NULL",
        )
        .unwrap();
    let mut paired = 0;
    let records = rows
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, usize>(2)?,
                r.get::<_, f32>(3)?,
                r.get::<_, Option<f32>>(4)?,
            ))
        })
        .unwrap();
    for record in records {
        let (p, q, actual, bits, candidate_bits) = record.unwrap();
        let p: Vec<f32> = serde_json::from_str(&p).unwrap();
        assert!((surprise(&p, actual) - bits).abs() < 1e-6);
        if let Some(q) = q {
            paired += 1;
            let q: Vec<f32> = serde_json::from_str(&q).unwrap();
            assert!((surprise(&q, actual) - candidate_bits.unwrap()).abs() < 1e-6);
        }
    }
    assert!(paired > 0);
    let checkpoints: i64 = connection
        .query_row("SELECT count(*) FROM sensor_checkpoints", [], |r| r.get(0))
        .unwrap();
    assert_eq!(checkpoints as u64, engine.stats.promotions + 1);
    let (_, restored) = Store::open(&path, "simulation:7", &cfg, true).unwrap();
    assert_eq!(
        serde_json::to_string(&restored).unwrap(),
        serde_json::to_string(&engine.model).unwrap()
    );
    assert!(Store::open(&path, "simulation:99", &cfg, true).is_err());
}

#[test]
fn fake_hwmon_and_proc_stat_follow_linux_file_contracts() {
    let tmp = TempDir::new();
    let dir = tmp.0.join("hwmon6");
    fs::create_dir(&dir).unwrap();
    fs::write(dir.join("name"), "k10temp\n").unwrap();
    fs::write(dir.join("temp2_label"), "Tdie\n").unwrap();
    fs::write(dir.join("temp2_input"), "52000\n").unwrap();
    fs::write(dir.join("temp_bad_input"), "1\n").unwrap();
    let sensors = sensor::discover(&tmp.0).unwrap();
    assert_eq!(sensors.len(), 1);
    let selected = sensor::select_cpu(&sensors).unwrap();
    assert_eq!(
        sensor::read_hwmon(&selected, 1000, 0.5, 0.0).raw,
        Some(52000)
    );
    fs::write(dir.join("temp2_input"), "unreadable\n").unwrap();
    assert!(sensor::read_hwmon(&selected, 2000, 0.5, 0.0)
        .problem
        .is_some());
    let proc_stat = tmp.0.join("stat");
    let mut load = CpuLoad::default();
    fs::write(&proc_stat, "cpu 10 0 0 90 0 0 0 0 100 100\n").unwrap();
    assert_eq!(load.read(&proc_stat).unwrap(), 0.0);
    fs::write(&proc_stat, "cpu 30 0 0 110 0 0 0 0 999 999\n").unwrap();
    assert_eq!(load.read(&proc_stat).unwrap(), 0.5); // Guest columns were excluded.
}
