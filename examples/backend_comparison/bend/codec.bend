import Base

type Sample is Data:
  Missing{}
  Bytes{b0: U32, b1: U32, b2: U32, b3: U32}

def token_if(valid: Bool, raw: U32) -> U32:
  match valid:
    case True{}:
      (raw / 4000 : U32)
    case False{}:
      4294967295

# Input bytes are the canonical i32 little-endian serialization used by Rust.
# A negative i32 is >= 2^31 as U32 and is rejected by the same upper bound.
def token(b0: U32, b1: U32, b2: U32, b3: U32) -> U32:
  +raw = (b0 .|. (b1 << 8n) .|. (b2 << 16n) .|. (b3 << 24n) : U32)
  token_if((raw < 128000 : U32), raw)
