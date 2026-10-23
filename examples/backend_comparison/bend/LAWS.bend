# These are the proposed contract, not a specification supplied by the user.
import Base
import ./gate.bend as Gate

law not_ready:
  for busy: Bool
  for level: Gate.Level
  {Gate.select(False{}, busy, level) == Gate.Hold{} : Gate.Mode}

law already_busy:
  for ready: Bool
  for level: Gate.Level
  {Gate.select(ready, True{}, level) == Gate.Hold{} : Gate.Mode}

law calm_holds:
  for ready: Bool
  for busy: Bool
  {Gate.select(ready, busy, Gate.Calm{}) == Gate.Hold{} : Gate.Mode}

law heads_preserve_encoder:
  for -A: Data
  for +old: A
  for candidate: A
  {Gate.encoder(A, Gate.Heads{}, old, candidate) == old : A}

law hold_preserves_encoder:
  for -A: Data
  for +old: A
  for candidate: A
  {Gate.encoder(A, Gate.Hold{}, old, candidate) == old : A}

law drift_requests_full:
  {Gate.select(True{}, False{}, Gate.Drift{}) == Gate.Full{} : Gate.Mode}
