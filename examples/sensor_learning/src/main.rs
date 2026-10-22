use clap::{Parser, ValueEnum};
use n0m1_sensorlab::{
    engine::Engine,
    learning::{Config, Trained},
    sensor::{self, CpuLoad, Simulator},
    store::Store,
    Result,
};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, ValueEnum)]
enum Source {
    Simulate,
    Hwmon,
}

#[derive(Parser, Debug)]
#[command(
    version,
    about = "Tiny sensor token predictors with bounded continual learning"
)]
struct Args {
    #[arg(long, value_enum, default_value = "simulate")]
    source: Source,
    #[arg(long, default_value = "sensor-learning.db")]
    db: PathBuf,
    /// Number of observations; 0 runs until SIGINT/SIGTERM.
    #[arg(long, default_value_t = 3000)]
    steps: u64,
    /// Real-sensor sampling interval. Simulation uses virtual one-second ticks.
    #[arg(long, default_value_t = 1000, value_parser = clap::value_parser!(u64).range(100..))]
    interval_ms: u64,
    #[arg(long, default_value_t = 7)]
    seed: u64,
    /// Explicit hwmon tempN_input path (use --list-sensors to inspect labels).
    #[arg(long)]
    sensor: Option<PathBuf>,
    #[arg(long)]
    list_sensors: bool,
    /// Restore the latest compatible checkpoint. Buffers and controller restart.
    #[arg(long)]
    resume: bool,
    /// Forecast and measure surprise without adapting weights.
    #[arg(long)]
    no_train: bool,
    /// Per-job wall-clock cap for real input, in addition to the SGD step budget.
    #[arg(long, default_value_t = 100, value_parser = clap::value_parser!(u64).range(1..=10000))]
    max_train_ms: u64,
}

fn main() -> Result<()> {
    let args = Args::parse();
    let hwmon_root = std::path::Path::new("/sys/class/hwmon");
    if args.list_sensors {
        for s in sensor::discover(hwmon_root)? {
            println!("{}\t{}\t{}", s.driver, s.label, s.path.display());
        }
        return Ok(());
    }
    let selected = match args.source {
        Source::Simulate => {
            if args.sensor.is_some() {
                return Err("--sensor requires --source hwmon".into());
            }
            None
        }
        Source::Hwmon => {
            let sensors = sensor::discover(hwmon_root)?;
            Some(if let Some(path) = &args.sensor {
                sensors.into_iter().find(|s| &s.path == path).ok_or(
                    "--sensor must match a discovered tempN_input path from --list-sensors",
                )?
            } else {
                sensor::select_cpu(&sensors)?
            })
        }
    };
    let source_id = selected
        .as_ref()
        .map(|s| format!("hwmon:{}:{}:{}", s.driver, s.label, s.path.display()))
        .unwrap_or_else(|| format!("simulation:{}", args.seed));
    let cfg = Config::default();
    let (mut store, model) = Store::open(&args.db, &source_id, &cfg, args.resume)?;
    let mut engine = Engine::new(cfg, model);
    eprintln!(
        "run={} source={} parameters={} version={}",
        store.run_id,
        source_id,
        engine.model.parameter_count(),
        engine.model.version
    );
    let stopped = Arc::new(AtomicBool::new(false));
    let signal = stopped.clone();
    ctrlc::set_handler(move || signal.store(true, Ordering::Relaxed))?;
    let training = Arc::new(AtomicBool::new(false));
    let training_micros = Arc::new(AtomicU64::new(0));
    let mut worker: Option<thread::JoinHandle<Trained>> = None;
    let mut simulator = Simulator::new(args.seed);
    let mut cpu_load = CpuLoad::default();
    if selected.is_some() {
        let _ = cpu_load.read(std::path::Path::new("/proc/stat"))?;
    }
    let start = Instant::now();
    let mut previous_tick = Instant::now();
    let mut previous_training_micros = 0;
    while !stopped.load(Ordering::Relaxed) && (args.steps == 0 || engine.step < args.steps) {
        if worker.as_ref().is_some_and(thread::JoinHandle::is_finished) {
            let trained = worker
                .take()
                .unwrap()
                .join()
                .map_err(|_| "training worker panicked")?;
            engine.accept_trained(trained);
        }
        let tick = Instant::now();
        let reading = if let Some(sensor) = &selected {
            let spent = training_micros.load(Ordering::Relaxed);
            let elapsed_us = tick.duration_since(previous_tick).as_micros().max(1) as f32;
            let duty = if training.load(Ordering::Relaxed) {
                1.0
            } else {
                (spent.saturating_sub(previous_training_micros) as f32 / elapsed_us).clamp(0.0, 1.0)
            };
            previous_training_micros = spent;
            let load = cpu_load.read(std::path::Path::new("/proc/stat"))?;
            sensor::read_hwmon(sensor, start.elapsed().as_millis() as u64, load, duty)
        } else {
            simulator.read()
        };
        previous_tick = tick;
        let batch = engine.observe(reading);
        store.record(&batch)?;
        if let Some(code) = batch.forecasts.get(1).and_then(|p| p.code) {
            if engine.step.is_multiple_of(100) {
                eprintln!(
                    "step={} token={:?} code={} version={} promotions={}",
                    engine.step, batch.token, code, engine.model.version, engine.stats.promotions
                );
            }
        }
        if !args.no_train && (args.steps == 0 || engine.step < args.steps) {
            if let Some(job) = engine.training_job() {
                if selected.is_none() {
                    engine.accept_trained(job.run(None));
                } else {
                    let active = training.clone();
                    let micros = training_micros.clone();
                    let max_ms = args.max_train_ms;
                    worker = Some(thread::spawn(move || {
                        active.store(true, Ordering::Relaxed);
                        let begin = Instant::now();
                        let result = job.run(Some(max_ms));
                        micros.fetch_add(begin.elapsed().as_micros() as u64, Ordering::Relaxed);
                        active.store(false, Ordering::Relaxed);
                        result
                    }));
                }
            }
        }
        let events = engine.drain_events();
        for e in &events {
            if e.kind.starts_with("rejected") || e.kind == "promoted" {
                eprintln!("step={} {} {}", e.step, e.kind, e.detail);
            }
        }
        store.events(&events)?;
        if selected.is_some() {
            let deadline = tick + Duration::from_millis(args.interval_ms);
            // Short sleeps keep SIGTERM responsive; no accumulated catch-up burst.
            while !stopped.load(Ordering::Relaxed) && Instant::now() < deadline {
                thread::sleep(
                    deadline
                        .saturating_duration_since(Instant::now())
                        .min(Duration::from_millis(50)),
                );
            }
        }
    }
    // At most one bounded job exists. Never promote a candidate at shutdown
    // without its required future observations.
    if let Some(handle) = worker {
        engine.accept_trained(handle.join().map_err(|_| "training worker panicked")?);
    }
    engine.shutdown();
    store.events(&engine.drain_events())?;
    store.finish()?;
    let mean = |items: &[f32]| {
        if items.is_empty() {
            None
        } else {
            Some(items.iter().sum::<f32>() / items.len() as f32)
        }
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "run_id": store.run_id, "steps": engine.step, "version": engine.model.version,
            "valid": engine.stats.valid, "invalid": engine.stats.invalid,
            "attempts": engine.stats.attempts, "promotions": engine.stats.promotions, "rejections": engine.stats.rejections,
            "first_256_short_bits": mean(&engine.stats.first_short_bits),
            "last_256_short_bits": mean(&engine.stats.recent_short_bits.iter().copied().collect::<Vec<_>>()),
            "mean_bits": ([0,1].map(|i| if engine.stats.scored[i] == 0 { None } else {
                Some(engine.stats.total_bits[i] / engine.stats.scored[i] as f64)
            })),
        }))?
    );
    Ok(())
}
