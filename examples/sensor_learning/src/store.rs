use crate::{
    engine::{Batch, Event},
    learning::Config,
    model::Models,
    sensor::{bytes, CODEC},
    Result,
};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::{path::Path, time::Duration};

#[derive(Serialize, Deserialize)]
struct Checkpoint {
    format: u32,
    codec: String,
    source: String,
    config: Config,
    model: Models,
}
impl Checkpoint {
    fn validate(self, source: &str, cfg: &Config) -> Result<Models> {
        if self.format != 1
            || self.codec != CODEC
            || self.source != source
            || &self.config != cfg
            || !self.model.valid()
        {
            return Err(
                "checkpoint format, source, tokenizer, configuration, or model is incompatible"
                    .into(),
            );
        }
        Ok(self.model)
    }
}

pub struct Store {
    connection: Connection,
    pub run_id: i64,
    source: String,
    cfg: Config,
}
impl Store {
    pub fn open(path: &Path, source: &str, cfg: &Config, resume: bool) -> Result<(Self, Models)> {
        let connection = Connection::open(path)?;
        connection.busy_timeout(Duration::from_secs(2))?;
        connection.execute_batch("
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS sensor_runs (
                id INTEGER PRIMARY KEY, started TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT NOT NULL, codec TEXT NOT NULL, config TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sensor_readings (
                run_id INTEGER NOT NULL REFERENCES sensor_runs(id), step INTEGER NOT NULL,
                monotonic_ms INTEGER NOT NULL, raw_millic INTEGER, raw_bytes BLOB, token INTEGER,
                cpu_load REAL NOT NULL, training_duty REAL NOT NULL, problem TEXT,
                PRIMARY KEY(run_id, step)
            );
            CREATE TABLE IF NOT EXISTS sensor_predictions (
                run_id INTEGER NOT NULL REFERENCES sensor_runs(id), origin INTEGER NOT NULL,
                layer INTEGER NOT NULL, target INTEGER NOT NULL, version INTEGER NOT NULL,
                code INTEGER, probabilities TEXT NOT NULL, candidate_probabilities TEXT, actual INTEGER, surprise_bits REAL,
                candidate_bits REAL, attempt INTEGER, cancelled TEXT,
                PRIMARY KEY(run_id, origin, layer)
            );
            CREATE TABLE IF NOT EXISTS sensor_events (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES sensor_runs(id),
                step INTEGER NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sensor_checkpoints (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES sensor_runs(id),
                step INTEGER NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL
            );
        ")?;
        let model = if resume {
            let payload: Option<String> = connection
                .query_row(
                    "SELECT payload FROM sensor_checkpoints ORDER BY id DESC LIMIT 1",
                    [],
                    |r| r.get(0),
                )
                .optional()?;
            let checkpoint: Checkpoint =
                serde_json::from_str(&payload.ok_or("no checkpoint to resume")?)?;
            checkpoint.validate(source, cfg)?
        } else {
            Models::new()
        };
        connection.execute(
            "INSERT INTO sensor_runs(source,codec,config) VALUES(?1,?2,?3)",
            params![source, CODEC, serde_json::to_string(cfg)?],
        )?;
        let run_id = connection.last_insert_rowid();
        let store = Self {
            connection,
            run_id,
            source: source.into(),
            cfg: cfg.clone(),
        };
        store.save_checkpoint(0, &model)?;
        Ok((store, model))
    }

    fn checkpoint_json(&self, model: &Models) -> Result<String> {
        Ok(serde_json::to_string(&Checkpoint {
            format: 1,
            codec: CODEC.into(),
            source: self.source.clone(),
            config: self.cfg.clone(),
            model: model.clone(),
        })?)
    }
    fn save_checkpoint(&self, step: u64, model: &Models) -> Result<()> {
        self.connection.execute(
            "INSERT INTO sensor_checkpoints(run_id,step,version,payload) VALUES(?1,?2,?3,?4)",
            params![
                self.run_id,
                step,
                model.version,
                self.checkpoint_json(model)?
            ],
        )?;
        Ok(())
    }

    pub fn record(&mut self, batch: &Batch) -> Result<()> {
        let checkpoint = batch
            .checkpoint
            .as_ref()
            .map(|m| self.checkpoint_json(m))
            .transpose()?;
        let tx = self.connection.transaction()?;
        let raw_bytes = batch.reading.raw.map(|v| bytes(v).to_vec());
        tx.execute(
            "INSERT INTO sensor_readings VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            params![
                self.run_id,
                batch.step,
                batch.reading.millis,
                batch.reading.raw,
                raw_bytes,
                batch.token.map(|t| t as i64),
                batch.reading.load,
                batch.reading.training_duty,
                batch.reading.problem,
            ],
        )?;
        if batch.token.is_none() {
            tx.execute(
                "UPDATE sensor_predictions SET cancelled='invalid observation gap'
                WHERE run_id=?1 AND actual IS NULL AND cancelled IS NULL",
                [self.run_id],
            )?;
        }
        for s in &batch.scores {
            let updated = tx.execute("UPDATE sensor_predictions SET actual=?1, surprise_bits=?2, candidate_bits=?3, attempt=?4
                WHERE run_id=?5 AND origin=?6 AND layer=?7 AND target=?8 AND actual IS NULL AND cancelled IS NULL", params![
                s.actual as i64, s.bits, s.candidate_bits, s.attempt, self.run_id, s.origin, s.layer as i64, s.target,
            ])?;
            if updated != 1 {
                return Err("score has no unique, previously persisted forecast".into());
            }
        }
        for p in &batch.forecasts {
            tx.execute("INSERT INTO sensor_predictions(run_id,origin,layer,target,version,code,probabilities,candidate_probabilities,attempt)
                VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)", params![
                self.run_id, p.origin, p.layer as i64, p.target, p.version, p.code, serde_json::to_string(&p.probabilities)?,
                p.candidate_probabilities.as_ref().map(serde_json::to_string).transpose()?, p.attempt,
            ])?;
        }
        if let Some(payload) = checkpoint {
            tx.execute(
                "INSERT INTO sensor_checkpoints(run_id,step,version,payload) VALUES(?1,?2,?3,?4)",
                params![
                    self.run_id,
                    batch.step,
                    batch.checkpoint.as_ref().unwrap().version,
                    payload
                ],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn events(&mut self, events: &[Event]) -> Result<()> {
        let tx = self.connection.transaction()?;
        for e in events {
            tx.execute(
                "INSERT INTO sensor_events(run_id,step,kind,detail) VALUES(?1,?2,?3,?4)",
                params![
                    self.run_id,
                    e.step,
                    e.kind,
                    serde_json::to_string(&e.detail)?
                ],
            )?;
        }
        tx.commit()?;
        Ok(())
    }
    pub fn finish(&self) -> Result<()> {
        self.connection.execute(
            "UPDATE sensor_runs SET completed=1 WHERE id=?1",
            [self.run_id],
        )?;
        self.connection.execute(
            "UPDATE sensor_predictions SET cancelled='end of run'
            WHERE run_id=?1 AND actual IS NULL AND cancelled IS NULL",
            [self.run_id],
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn checkpoint_rejects_mismatched_contracts_and_bad_shapes() {
        let cfg = Config::default();
        let cp = || Checkpoint {
            format: 1,
            codec: CODEC.into(),
            source: "simulation:7".into(),
            config: cfg.clone(),
            model: Models::new(),
        };
        assert!(cp().validate("simulation:7", &cfg).is_ok());
        assert!(cp().validate("hwmon:k10temp:Tdie", &cfg).is_err());
        let mut bad = cp();
        bad.codec = "different".into();
        assert!(bad.validate("simulation:7", &cfg).is_err());
        let mut changed = cfg.clone();
        changed.horizon = 9;
        assert!(cp().validate("simulation:7", &changed).is_err());
        let mut value = serde_json::to_value(cp()).unwrap();
        value["model"]["short"]["w1"] = serde_json::json!([]);
        assert!(serde_json::from_value::<Checkpoint>(value)
            .unwrap()
            .validate("simulation:7", &cfg)
            .is_err());
    }
}
