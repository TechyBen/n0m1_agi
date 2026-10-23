import Base

# A proposed discrete policy boundary. The host computes readiness and severity.
type Level is Data:
  Calm{}
  Watch{}
  Drift{}

type Mode is Data:
  Hold{}
  Heads{}
  Full{}

def select(ready: Bool, busy: Bool, level: Level) -> Mode:
  match ready busy level:
    case False{} _ _:
      Hold{}
    case True{} True{} _:
      Hold{}
    case True{} False{} Calm{}:
      Hold{}
    case True{} False{} Watch{}:
      Heads{}
    case True{} False{} Drift{}:
      Full{}

def encoder(-A: Data, mode: Mode, old: A, candidate: A) -> A:
  match mode:
    case Hold{}:
      old
    case Heads{}:
      old
    case Full{}:
      candidate

def number(mode: Mode) -> U32:
  match mode:
    case Hold{}:
      0
    case Heads{}:
      1
    case Full{}:
      2
