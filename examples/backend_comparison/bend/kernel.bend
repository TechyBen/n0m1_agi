import Base
import ./gate.bend as Gate

# Lists make the first port inspectable. This is a correctness baseline, not a
# claim that pointer-based lists are an efficient neural GPU representation.
type Net is Data:
  Net{discrete: Bool, w1: List<&2, List<&2, F32>>, b1: List<&2, F32>,
      w2: List<&2, List<&2, F32>>, b2: List<&2, F32>}

type Forward is Data:
  Forward{hidden: List<&2, F32>, code: List<&2, F32>, p: List<&2, F32>}

type Case is Data:
  Case{id: U32, net: Net, x: List<&2, F32>, y: Nat}

type Cases is Data:
  Leaf{value: Case}
  Fork{left: Cases, right: Cases}

type Row is Data:
  Row{id: U32, mode: U32, code: U32, argmax: U32, surprise: F32,
      p: List<&2, F32>, net: Net}

type Output is Data:
  Rows{before: Row, heads: Row, full: Row}
  Branch{left: Output, right: Output}

def dot(xs: List<&2, F32>, ys: List<&2, F32>, acc: F32) -> F32:
  match xs ys:
    case Con{x, xt} Con{y, yt}:
      dot(xt, yt, (acc + x * y : F32))
    case _ _:
      acc

def sum(xs: List<&2, F32>, acc: F32) -> F32:
  match xs:
    case Nil{}:
      acc
    case Con{x, xt}:
      sum(xt, (acc + x : F32))

def norm2(xs: List<&2, F32>, acc: F32) -> F32:
  match xs:
    case Nil{}:
      acc
    case Con{+x, xt}:
      norm2(xt, (acc + x * x : F32))

def at(xs: List<&2, F32>, n: Nat) -> F32:
  match xs n:
    case Con{x, _} 0n:
      x
    case Con{_, xt} 1n+p:
      at(xt, p)
    case Nil{} _:
      0.0

def linear(rows: List<&2, List<&2, F32>>, bias: List<&2, F32>, +x: List<&2, F32>) -> List<&2, F32>:
  match rows bias:
    case Con{w, wt} Con{b, bt}:
      (b + dot(w, x, 0.0) : F32) <> linear(wt, bt, x)
    case _ _:
      Nil{}

def tanhs(xs: List<&2, F32>) -> List<&2, F32>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{x, xt}:
      F32.tanh(x) <> tanhs(xt)

def sign(positive: Bool) -> F32:
  match positive:
    case True{}:
      1.0
    case False{}:
      F32.neg(1.0)

def signs(xs: List<&2, F32>) -> List<&2, F32>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{x, xt}:
      sign(F32.is_ge(x, 0.0)) <> signs(xt)

def encode(discrete: Bool, h: List<&2, F32>) -> List<&2, F32>:
  match discrete:
    case True{}:
      signs(h)
    case False{}:
      h

def maximum(xs: List<&2, F32>, acc: F32) -> F32:
  match xs:
    case Nil{}:
      acc
    case Con{x, xt}:
      maximum(xt, F32.max(acc, x))

def max_first(xs: List<&2, F32>) -> F32:
  match xs:
    case Nil{}:
      0.0
    case Con{x, xt}:
      maximum(xt, x)

def exps(xs: List<&2, F32>, +m: F32) -> List<&2, F32>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{x, xt}:
      F32.exp((x - m : F32)) <> exps(xt, m)

def divide(xs: List<&2, F32>, +divisor: F32) -> List<&2, F32>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{x, xt}:
      (x / divisor : F32) <> divide(xt, divisor)

def forward(net: Net, x: List<&2, F32>) -> Forward:
  Net{discrete, w1, b1, w2, b2} = net
  +h = tanhs(linear(w1, b1, x))
  +code = encode(discrete, h)
  +logits = linear(w2, b2, code)
  +e = exps(logits, max_first(logits))
  Forward{h, code, divide(e, sum(e, 0.0))}

def delta_value(hit: Bool, x: F32) -> F32:
  match hit:
    case True{}:
      (x - 1.0 : F32)
    case False{}:
      x

def delta(xs: List<&2, F32>, +target: Nat, +index: Nat) -> List<&2, F32>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{x, xt}:
      delta_value(Nat.is_eq(target, index), x) <> delta(xt, target, 1n+index)

def column_dot(ds: List<&2, F32>, rows: List<&2, List<&2, F32>>, +j: Nat, acc: F32) -> F32:
  match ds rows:
    case Con{d, dt} Con{row, rt}:
      column_dot(dt, rt, j, (acc + d * at(row, j) : F32))
    case _ _:
      acc

def hidden_delta(hs: List<&2, F32>, +ds: List<&2, F32>, +rows: List<&2, List<&2, F32>>, +j: Nat) -> List<&2, F32>:
  match hs:
    case Nil{}:
      Nil{}
    case Con{+h, ht}:
      (column_dot(ds, rows, j, 0.0) * (1.0 - h * h) : F32) <>
        hidden_delta(ht, ds, rows, 1n+j)

def norm_scope(mode: Gate.Mode, head: F32, enc: F32) -> F32:
  match mode:
    case Gate.Full{}:
      (head + enc : F32)
    case _:
      head

def update_vector(ws: List<&2, F32>, ds: List<&2, F32>, +rate: F32) -> List<&2, F32>:
  match ws ds:
    case Con{w, wt} Con{d, dt}:
      (w - rate * d : F32) <> update_vector(wt, dt, rate)
    case _ _:
      Nil{}

def update_row(ws: List<&2, F32>, xs: List<&2, F32>, +rd: F32) -> List<&2, F32>:
  match ws xs:
    case Con{w, wt} Con{x, xt}:
      (w - rd * x : F32) <> update_row(wt, xt, rd)
    case _ _:
      Nil{}

def update_matrix(rows: List<&2, List<&2, F32>>, ds: List<&2, F32>, +xs: List<&2, F32>, +rate: F32) -> List<&2, List<&2, F32>>:
  match rows ds:
    case Con{row, rt} Con{d, dt}:
      update_row(row, xs, (rate * d : F32)) <> update_matrix(rt, dt, xs, rate)
    case _ _:
      Nil{}

def train_forward(net: Net, +x: List<&2, F32>, y: Nat, rate: F32, +mode: Gate.Mode, f: Forward) -> Net:
  match net f:
    case Net{discrete, +w1, +b1, +w2, +b2} Forward{h, +code, p}:
      +ds = delta(p, y, 0n)
      +dh = hidden_delta(h, ds, w2, 0n)
      norm = norm_scope(mode,
        (norm2(ds, 0.0) * (1.0 + norm2(code, 0.0)) : F32),
        (norm2(dh, 0.0) * (1.0 + norm2(x, 0.0)) : F32))
      +r = (rate / F32.max(F32.sqrt(norm), 1.0) : F32)
      nw1 = Gate.encoder(List<&2, List<&2, F32>>, mode, w1, update_matrix(w1, dh, x, r))
      nb1 = Gate.encoder(List<&2, F32>, mode, b1, update_vector(b1, dh, r))
      Net{discrete, nw1, nb1, update_matrix(w2, ds, code, r), update_vector(b2, ds, r)}

def train(+net: Net, +x: List<&2, F32>, y: Nat, rate: F32, mode: Gate.Mode) -> Net:
  match mode:
    case Gate.Hold{}:
      net
    case Gate.Heads{}:
      train_forward(net, x, y, rate, Gate.Heads{}, forward(net, x))
    case Gate.Full{}:
      train_forward(net, x, y, rate, Gate.Full{}, forward(net, x))

def choose_index(better: Bool, old: U32, new: U32) -> U32:
  match better:
    case True{}:
      new
    case False{}:
      old

def argmax(xs: List<&2, F32>, +best: F32, best_index: U32, +index: U32) -> U32:
  match xs:
    case Nil{}:
      best_index
    case Con{+x, xt}:
      argmax(xt, F32.max(best, x), choose_index(F32.is_gt(x, best), best_index, index), (index + 1 : U32))

def code(xs: List<&2, F32>, +bit: U32, acc: U32) -> U32:
  match xs:
    case Nil{}:
      acc
    case Con{x, xt}:
      code(xt, (bit * 2 : U32), (acc + choose_index(F32.is_gt(x, 0.0), 0, bit) : U32))

def code_for(discrete: Bool, xs: List<&2, F32>) -> U32:
  match discrete:
    case False{}:
      4294967295
    case True{}:
      code(xs, 1, 0)

def is_discrete(net: Net) -> Bool:
  Net{discrete, _, _, _, _} = net
  discrete

def report_forward(id: U32, mode: U32, net: Net, y: Nat, discrete: Bool, f: Forward) -> Row:
  match f:
    case Forward{_, c, +p}:
      Row{id, mode, code_for(discrete, c), argmax(p, F32.neg(1.0), 0, 0),
        F32.neg(F32.log2(F32.max(at(p, y), 0.000000001))), p, net}

def report(id: U32, mode: U32, +net: Net, x: List<&2, F32>, y: Nat) -> Row:
  report_forward(id, mode, net, y, is_discrete(net), forward(net, x))

def run_case(value: Case, +rate: F32) -> Output:
  Case{+id, +net, +x, +y} = value
  heads full = train(net, x, y, rate, Gate.Heads{}) train(net, x, y, rate, Gate.Full{})
  Rows{report(id, 0, net, x, y), report(id, 1, heads, x, y), report(id, 2, full, x, y)}

def batch(cases: Cases, +rate: F32) -> Output:
  match cases:
    case Leaf{c}:
      run_case(c, rate)
    case Fork{a, b}:
      left right = batch(a, rate) batch(b, rate)
      Branch{left, right}
