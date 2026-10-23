//! The existing Rust model is the oracle; Bend receives serialized weights,
//! never a second implementation of the random initializer.
use n0m1_sensorlab::{
    model::{surprise, Example, Models, Net, Scope, INPUTS},
    sensor::{bytes, tokenize},
    Result,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{env, fs};

#[derive(Serialize, Deserialize)]
struct Case {
    id: usize,
    net: Net,
    discrete: bool,
    x: Vec<f32>,
    y: usize,
}
#[derive(Serialize, Deserialize)]
struct Fixture {
    schema: u32,
    rate: f32,
    raw: Vec<Option<i32>>,
    cases: Vec<Case>,
}
fn fixture() -> Fixture {
    let models = Models::new();
    let mut cases = Vec::new();
    for discrete in [false, true] {
        for sample in 0..4 {
            let mut x = vec![0.0; INPUTS];
            if sample > 0 {
                x[9 + sample] = 1.0;
                x[32] = 0.31 + sample as f32 * 0.09;
                x[33] = -0.03;
                x[34] = 0.85;
                x[35] = 0.1;
                x[36] = 0.0;
                x[37] = 0.5;
                x[38] = if discrete { 0.4 } else { 0.0 };
                x[39] = 1.0;
            }
            if sample == 3 {
                // Deliberately large feature exercises global gradient clipping.
                x[33] = 8.0;
            }
            cases.push(Case {
                id: cases.len(),
                net: if discrete {
                    models.long.clone()
                } else {
                    models.short.clone()
                },
                discrete,
                x,
                y: 9 + sample,
            });
        }
    }
    Fixture {
        schema: 1,
        rate: 0.03,
        raw: vec![
            None,
            Some(i32::MIN),
            Some(-1),
            Some(0),
            Some(3999),
            Some(4000),
            Some(40000),
            Some(127999),
            Some(128000),
            Some(i32::MAX),
        ],
        cases,
    }
}
fn row(c: &Case, mode: &str, net: &Net) -> Value {
    let p = net.predict(&c.x);
    // The first maximum wins, including exact ties.
    let mut argmax = 0;
    for i in 1..p.len() {
        if p[i] > p[argmax] {
            argmax = i;
        }
    }
    json!({"id": c.id, "mode": mode,
        "code": if c.discrete { i32::from(net.token(&c.x)) } else { -1 },
        "argmax": argmax, "surprise": surprise(&p, c.y),
        "probabilities": p, "parameters": net.parameters().copied().collect::<Vec<_>>()})
}
fn evaluate(f: Fixture) -> Result<Value> {
    if f.schema != 1 || !f.rate.is_finite() || f.rate <= 0.0 {
        return Err("invalid fixture schema/rate".into());
    }
    let mut rows = Vec::new();
    for (index, c) in f.cases.iter().enumerate() {
        if c.id != index
            || !c.net.valid(if c.discrete { 4 } else { 16 }, c.discrete)
            || c.x.len() != INPUTS
            || c.x.iter().any(|v| !v.is_finite())
            || c.y >= 32
        {
            return Err("invalid model/feature fixture".into());
        }
        rows.push(row(c, "before", &c.net));
        for (mode, scope) in [("heads", Scope::Heads), ("full", Scope::Full)] {
            let mut trained = c.net.clone();
            trained.train_one(
                &Example {
                    x: c.x.clone(),
                    y: c.y,
                },
                f.rate,
                scope,
            );
            rows.push(row(c, mode, &trained));
        }
    }
    let codec: Vec<_> = f
        .raw
        .into_iter()
        .map(|r| {
            json!({
                "bytes": r.map(bytes), "token": r.and_then(tokenize).map(|n| n as i32).unwrap_or(-1)
            })
        })
        .collect();
    // Abstract discrete gate: eligibility/candidate status are supplied by the
    // host. This is not a duplicate of Controller's EWMA/streak calculations.
    let mut gates = Vec::new();
    for ready in [false, true] {
        for busy in [false, true] {
            for level in 0..3 {
                gates.push(if ready && !busy { level } else { 0 });
            }
        }
    }
    Ok(json!({"schema": 1, "codec": codec, "gates": gates, "rows": rows}))
}
fn main() -> Result<()> {
    let args: Vec<_> = env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("export") if args.len() == 3 => {
            fs::write(&args[2], serde_json::to_vec_pretty(&fixture())?)?;
        }
        Some("run") if args.len() == 3 => {
            let f: Fixture = serde_json::from_slice(&fs::read(&args[2])?)?;
            println!("{}", serde_json::to_string(&evaluate(f)?)?);
        }
        _ => return Err("usage: backend_reference export|run FIXTURE.json".into()),
    }
    Ok(())
}
