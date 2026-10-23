import Base
import ./gate.bend as Gate
import ./LAWS.bend as Laws

def Laws.not_ready(busy, level):
  {==}

def Laws.already_busy(ready, level):
  match ready:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.calm_holds(ready, busy):
  match ready busy:
    case False{} _:
      {==}
    case True{} True{}:
      {==}
    case True{} False{}:
      {==}

def Laws.heads_preserve_encoder(A, old, candidate):
  {==}

def Laws.hold_preserves_encoder(A, old, candidate):
  {==}

def Laws.drift_requests_full():
  {==}
