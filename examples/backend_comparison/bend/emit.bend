import Base
import ./kernel.bend as K
import ./codec.bend as C
import ./gate.bend as G

def floats(xs: List<&2, F32>) -> String:
  List.show(~&2, ~F32, ~F32.show, xs)

def flatten(rows: List<&2, List<&2, F32>>) -> List<&2, F32>:
  match rows:
    case Nil{}:
      Nil{}
    case Con{h, t}:
      List.append(&2, F32, h, flatten(t))

def parameters(net: K.Net) -> List<&2, F32>:
  K.Net{_, w1, b1, w2, b2} = net
  List.append(&2, F32, flatten(w1), List.append(&2, F32, b1,
    List.append(&2, F32, flatten(w2), b2)))

def mode_name(mode: U32) -> String:
  match mode:
    case 0:
      "before"
    case 1:
      "heads"
    case _:
      "full"

def signed_token(token: U32) -> String:
  match token:
    case 4294967295:
      "-1"
    case _:
      U32.show(token)

def row(value: K.Row) -> String:
  K.Row{id, mode, code, argmax, surprise, p, net} = value
  "{\"id\":" ++ U32.show(id) ++ ",\"mode\":\"" ++ mode_name(mode) ++
  "\",\"code\":" ++ signed_token(code) ++ ",\"argmax\":" ++ U32.show(argmax) ++
  ",\"surprise\":" ++ F32.show(surprise) ++ ",\"probabilities\":" ++ floats(p) ++
  ",\"parameters\":" ++ floats(parameters(net)) ++ "}"

def rows(output: K.Output) -> String:
  match output:
    case K.Rows{a, b, c}:
      row(a) ++ "," ++ row(b) ++ "," ++ row(c)
    case K.Branch{a, b}:
      rows(a) ++ "," ++ rows(b)

def sample(value: C.Sample) -> String:
  match value:
    case C.Missing{}:
      "{\"bytes\":null,\"token\":-1}"
    case C.Bytes{+a, +b, +c, +d}:
      "{\"bytes\":[" ++ U32.show(a) ++ "," ++ U32.show(b) ++ "," ++
        U32.show(c) ++ "," ++ U32.show(d) ++ "],\"token\":" ++
        signed_token(C.token(a, b, c, d)) ++ "}"

def samples_tail(xs: List<&2, C.Sample>) -> String:
  match xs:
    case Nil{}:
      ""
    case Con{x, xt}:
      "," ++ sample(x) ++ samples_tail(xt)

def samples(xs: List<&2, C.Sample>) -> String:
  match xs:
    case Nil{}:
      "[]"
    case Con{x, xt}:
      "[" ++ sample(x) ++ samples_tail(xt) ++ "]"

def gate_three(+ready: Bool, +busy: Bool) -> String:
  U32.show(G.number(G.select(ready, busy, G.Calm{}))) ++ "," ++
  U32.show(G.number(G.select(ready, busy, G.Watch{}))) ++ "," ++
  U32.show(G.number(G.select(ready, busy, G.Drift{})))

def gates() -> String:
  "[" ++ gate_three(False{}, False{}) ++ "," ++ gate_three(False{}, True{}) ++
  "," ++ gate_three(True{}, False{}) ++ "," ++ gate_three(True{}, True{}) ++ "]"

def document(output: K.Output, raw: List<&2, C.Sample>) -> String:
  "{\"schema\":1,\"codec\":" ++ samples(raw) ++ ",\"gates\":" ++ gates() ++
    ",\"rows\":[" ++ rows(output) ++ "]}"
