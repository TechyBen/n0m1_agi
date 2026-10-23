#!/usr/bin/env python3
"""Compile one pinned fixture and compare Rust/Bend behavior. Python stdlib only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import statistics
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
RUST = ROOT.parent / "sensor_learning"
PIN = json.loads((ROOT / "toolchain.json").read_text())
MODES = ("before", "heads", "full")


def read_json(path):
    def bad(value):
        raise ValueError(f"non-finite JSON value: {value}")
    return json.loads(Path(path).read_text(), parse_constant=bad)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hash():
    files = sorted((ROOT / "bend").glob("*.bend")) + [
        ROOT / "harness.py", ROOT / "toolchain.json",
        RUST / "examples/backend_reference.rs", RUST / "src/model.rs",
        RUST / "src/sensor.rs", RUST / "Cargo.lock",
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT.parent)).encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def literal(x):
    value = f32(x)
    if not math.isfinite(value):
        raise ValueError("fixture contains a non-finite value")
    # Both parsers round this decimal to the same f32, including signed zero.
    s = format(abs(value), ".9g")
    if "e" in s.lower():
        # Bend literals use decimal notation; avoid relying on exponent syntax.
        s = format(abs(value), ".60f").rstrip("0")
    if "." not in s:
        s += ".0"
    return f"F32.neg({s})" if math.copysign(1, value) < 0 else s


def vector(xs):
    return "[" + ", ".join(literal(x) for x in xs) + "]"


def matrix(xs, width):
    return "[" + ",\n    ".join(vector(xs[i:i+width]) for i in range(0, len(xs), width)) + "]"


def validate_fixture(f):
    if f["schema"] != 1 or not 0 < f["rate"] < 1 or len(f["cases"]) != 8:
        raise ValueError("expected schema 1, eight cases and a valid rate")
    for i, c in enumerate(f["cases"]):
        n = c["net"]
        h = 4 if c["discrete"] else 16
        if (c["id"] != i or type(c["discrete"]) is not bool or n["hidden"] != h
                or n["discrete"] != c["discrete"] or len(c["x"]) != 40
                or type(c["y"]) is not int or not 0 <= c["y"] < 32):
            raise ValueError(f"bad case {i}")
        for field, size in (("w1", h*40), ("b1", h), ("w2", 32*h), ("b2", 32)):
            if len(n[field]) != size:
                raise ValueError(f"bad {field} size in case {i}")
        for x in c["x"] + n["w1"] + n["b1"] + n["w2"] + n["b2"]:
            if not math.isfinite(x) or abs(x) >= 1000:
                raise ValueError(f"bad numeric value in case {i}")
    for raw in f["raw"]:
        if raw is not None and (type(raw) is not int or not -(2**31) <= raw < 2**31):
            raise ValueError("raw values must be missing or signed i32")


def prepare(out):
    fixture = read_json(ROOT / "fixture.json")
    validate_fixture(fixture)
    dest = Path(out).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for p in (ROOT / "bend").glob("*.bend"):
        shutil.copyfile(p, dest / p.name)
    defs = ["# Generated from fixture.json by harness.py; do not edit.\n",
            "import Base\nimport ./kernel.bend as K\nimport ./codec.bend as C\n"]
    for c in fixture["cases"]:
        n = c["net"]
        h = n["hidden"]
        defs.append(f"\ndef case_{c['id']}() -> K.Case:\n"
                    f"  K.Case{{{c['id']}, K.Net{{{'True{}' if c['discrete'] else 'False{}'},\n"
                    f"    {matrix(n['w1'], 40)}, {vector(n['b1'])},\n"
                    f"    {matrix(n['w2'], h)}, {vector(n['b2'])}}},\n"
                    f"    {vector(c['x'])}, {c['y']}n}}\n")
    def tree(ids):
        if len(ids) == 1:
            return f"K.Leaf{{case_{ids[0]}()}}"
        mid = len(ids)//2
        return f"K.Fork{{{tree(ids[:mid])}, {tree(ids[mid:])}}}"
    defs.append(f"\ndef cases() -> K.Cases:\n  {tree(list(range(8)))}\n")
    raw = ["C.Missing{}" if v is None else "C.Bytes{" + ", ".join(
        str(b) for b in struct.pack("<i", v)) + "}" for v in fixture["raw"]]
    defs.append("\ndef raw() -> List<&2, C.Sample>:\n  [" + ", ".join(raw) + "]\n")
    defs.append(f"\ndef rate() -> F32:\n  {literal(fixture['rate'])}\n")
    (dest / "fixture.bend").write_text("".join(defs))
    for backend, bang in (("cpu", ""), ("metal", "!")):
        (dest / f"{backend}.bend").write_text(
            "import Base\nimport ./kernel.bend as K\nimport ./fixture.bend as F\n"
            "import ./emit.bend as E\n\ndef main() -> IO(Unit):\n"
            f"  IO.print(E.document(K.batch{bang}(F.cases(), F.rate()), F.raw()))\n")
    return dest


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: expected a finite number")


def validate_result(result, fixture):
    if result.get("schema") != 1 or len(result.get("rows", [])) != 24:
        raise ValueError("result schema/row count mismatch")
    if len(result.get("codec", [])) != len(fixture["raw"]):
        raise ValueError("codec row count mismatch")
    if result.get("gates") != [0, 0, 0, 0, 0, 0, 0, 1, 2, 0, 0, 0]:
        raise ValueError("discrete gate truth table mismatch")
    for source, row in zip(fixture["raw"], result["codec"]):
        expected = {"bytes": None if source is None else list(struct.pack("<i", source)),
                    "token": source//4000 if source is not None and 0 <= source < 128000 else -1}
        if row != expected:
            raise ValueError(f"codec mismatch for {source}")
    for c in fixture["cases"]:
        rows = result["rows"][c["id"]*3:c["id"]*3+3]
        for mode, row in zip(MODES, rows):
            if row.get("id") != c["id"] or row.get("mode") != mode:
                raise ValueError("missing, duplicated or reordered row")
            if type(row.get("argmax")) is not int or not 0 <= row["argmax"] < 32:
                raise ValueError("invalid argmax")
            if type(row.get("code")) is not int or row["code"] not in (range(16) if c["discrete"] else [-1]):
                raise ValueError("invalid discrete code")
            if len(row.get("probabilities", [])) != 32 or len(row.get("parameters", [])) != (324 if c["discrete"] else 1200):
                raise ValueError("numeric vector length mismatch")
            for key in ("probabilities", "parameters"):
                for x in row[key]:
                    finite(x, key)
            finite(row.get("surprise"), "surprise")
            p = row["probabilities"]
            if any(x < 0 or x > 1 for x in p) or abs(sum(p)-1) > 1e-5:
                raise ValueError("invalid probability distribution")
            # The source chooses the first maximum. Parsing decimal f32 text
            # back through f32 also preserves ties in either language's output.
            pf = [f32(x) for x in p]
            if row["argmax"] != max(range(32), key=pf.__getitem__):
                raise ValueError("argmax does not match probabilities")
            expected_bits = -math.log2(max(p[c["y"]], 1e-9))
            if abs(row["surprise"]-expected_bits) > 2e-5:
                raise ValueError("surprise is not the observed target's probability")
        encoder_size = (4 if c["discrete"] else 16)*41
        if [struct.pack("<f", x) for x in rows[0]["parameters"][:encoder_size]] != [struct.pack("<f", x) for x in rows[1]["parameters"][:encoder_size]]:
            raise ValueError("heads-only update changed its encoder")
        initial = sum((c["net"][key] for key in ("w1", "b1", "w2", "b2")), [])
        if [struct.pack("<f", x) for x in initial] != [struct.pack("<f", x) for x in rows[0]["parameters"]]:
            raise ValueError("initial weights differ from the fixture")
        for row in rows[1:]:
            displacement = math.sqrt(sum((a-b)**2 for a, b in zip(row["parameters"], initial)))
            if displacement > fixture["rate"] + 1e-6:
                raise ValueError("update exceeds the clipped step bound")


def compare_results(reference, candidate, fixture):
    validate_result(reference, fixture)
    validate_result(candidate, fixture)
    if reference["codec"] != candidate["codec"] or reference["gates"] != candidate["gates"]:
        raise ValueError("discrete codec/gate mismatch")
    max_abs = 0.0
    count = 0
    for a, b in zip(reference["rows"], candidate["rows"]):
        label = f"case {a['id']} {a['mode']}"
        for key in ("id", "mode", "code", "argmax"):
            if a[key] != b[key]:
                raise ValueError(f"{label}: discrete {key} changed: {a[key]} -> {b[key]}")
        for key in ("surprise", "probabilities", "parameters"):
            av = a[key] if isinstance(a[key], list) else [a[key]]
            bv = b[key] if isinstance(b[key], list) else [b[key]]
            for i, (x, y) in enumerate(zip(av, bv)):
                error = abs(x-y)
                if error > PIN["absolute_tolerance"] + PIN["relative_tolerance"]*abs(x):
                    raise ValueError(f"{label}: {key}[{i}] differs: {x} vs {y}")
                max_abs = max(max_abs, error)
                count += 1
    return {"pass": True, "numeric_values": count, "max_absolute_error": max_abs,
            "absolute_tolerance": PIN["absolute_tolerance"], "relative_tolerance": PIN["relative_tolerance"]}


def command(argv, *, cwd=ROOT, timeout=180):
    # Literal argument arrays, never shell command execution. Clear inherited
    # runtime flags so --gpu/--threads mean exactly what the report records.
    env = {**os.environ, "BENDRTS": "", "BEND_NO_TELEMETRY": "1"}
    proc = subprocess.run([str(a) for a in argv], cwd=cwd, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(f"{shlex.join([str(a) for a in argv])}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def metadata():
    info = {"os": platform.platform(), "architecture": platform.machine(),
            "python": platform.python_version(), "logical_cpus": os.cpu_count()}
    if sys.platform == "darwin":
        info["cpu"] = command(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        info["metal_devices"] = json.loads(command(["system_profiler", "SPDisplaysDataType", "-json"]))
    elif Path("/proc/cpuinfo").exists():
        info["cpu"] = next((x.split(":", 1)[1].strip() for x in Path("/proc/cpuinfo").read_text().splitlines()
                            if x.startswith("model name")), "unknown")
    return info


def choose_clang(backend):
    candidates = [os.environ.get("CC"), shutil.which("clang")]
    numbered = set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(directory)
        if p.is_dir():
            numbered.update(str(f) for f in p.glob("clang-*") if re.fullmatch(r"clang-\d+", f.name))
    candidates += sorted(numbered, key=lambda p: int(p.rsplit("-", 1)[1]), reverse=True)
    for candidate in dict.fromkeys(c for c in candidates if c):
        try:
            version = command([candidate, "--version"])
        except (OSError, RuntimeError):
            continue
        match = re.search(r"^(Apple )?(?:\w+ )?clang version (\d+)", version, re.M)
        minimum = (17 if match and match[1] else 19) if backend == "metal" else 14
        if match and int(match[2]) >= minimum:
            return candidate, version.strip()
    raise ValueError("no suitable Clang found; CPU needs 14+, Metal needs Apple Clang 17+ or Clang 19+")


def run(args):
    source = Path(args.bend_source).resolve()
    commit = command(["git", "rev-parse", "HEAD"], cwd=source).strip()
    if commit != PIN["bend_commit"] or command(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source).strip():
        raise ValueError("Bend checkout must be clean and at the pinned commit")
    if args.backend == "metal" and (sys.platform != "darwin" or platform.machine() != "arm64"):
        raise ValueError("this test's Metal lane requires a native Apple Silicon macOS host")
    if args.threads < 1 or args.repetitions < 1:
        raise ValueError("threads and repetitions must be positive")
    bend = shlex.split(args.bend_command) if args.bend_command else [args.bun, str(source / "bend2/main.ts")]
    clang, clang_version = choose_clang(args.backend)
    os.environ["CC"] = clang
    build = prepare(ROOT / ".build" / args.backend)
    print("Building Rust reference and checking Bend laws...", flush=True)
    command([args.cargo, "build", "--locked", "--release", "--example", "backend_reference"], cwd=RUST, timeout=300)
    reference = Path(args.reference).resolve() if args.reference else RUST / "target/release/examples/backend_reference"
    fixture = read_json(ROOT / "fixture.json")
    expected = json.loads(command([reference, "run", ROOT / "fixture.json"]))
    validate_result(expected, fixture)
    proof_log = command(bend + [build / "PROOF.bend", "--verdict" if args.verdict else "--check-only"], timeout=600)
    if args.verdict and "ALL PROOFS CHECK" not in proof_log:
        raise ValueError("missing BendTT success verdict")
    binary = build / "probe"
    print(f"Compiling {args.backend} probe...", flush=True)
    command(bend + [build / f"{args.backend}.bend", "-o", binary], timeout=300)
    sidecar = Path(str(binary) + ".gpu")
    if args.backend == "metal" and not sidecar.is_file():
        raise ValueError("Metal build produced no .gpu sidecar")
    # A positive heap size requests GPU explicitly. The pinned runtime fails
    # if unavailable; auto mode would allow silent fallback. Keep the root ! call.
    runtime = [binary, "+RTS", "--threads", str(args.threads), "--gpu",
               "256MB" if args.backend == "metal" else "off"]
    durations = []
    candidate = None
    checks = []
    for iteration in range(args.repetitions+1):
        start = time.perf_counter()
        candidate = json.loads(command(runtime, timeout=180))
        duration = time.perf_counter()-start
        checks.append(compare_results(expected, candidate, fixture))
        if iteration:  # one process warmup, excluded from reported timings
            durations.append(duration)
    # Every repetition is validated, not just the fastest or final one.
    report = {"report_schema": 1, "fixture_sha256": sha(ROOT / "fixture.json"),
              "source_sha256": source_hash(), "bend_commit": commit,
              "bend_version": command(bend + ["version"]).strip(),
              "compiler_command": bend, "runtime_arguments": [str(x) for x in runtime[1:]],
              "backend": args.backend, "machine": metadata(),
              "rustc": command([args.rustc, "--version"]).strip(),
              "clang": {"command": clang, "version": clang_version},
              "proof": {"mode": "bendtt-verdict" if args.verdict else "source-check",
                        "log": proof_log.strip()},
              "gpu": {"required": args.backend == "metal", "sidecar_sha256": sha(sidecar) if args.backend == "metal" else None,
                      "evidence": "forced runtime request plus root ! call; not a profiler trace" if args.backend == "metal" else "disabled"},
              "timing": {"kind": "process wall time including startup, output and synchronization",
                         "warmups": 1, "samples_seconds": durations,
                         "median_seconds": statistics.median(durations)},
              "checks": checks, "reference": expected, "result": candidate}
    path = Path(args.out).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"report": str(path), "checked_runs": len(checks),
                      "max_absolute_error": max(c["max_absolute_error"] for c in checks),
                      "median_seconds": report["timing"]["median_seconds"]}, indent=2))


def compare_reports(paths):
    reports = [read_json(p) for p in paths]
    if len(reports) < 2:
        raise ValueError("supply at least two reports")
    fixture = read_json(ROOT / "fixture.json")
    first = reports[0]
    for report in reports:
        if report.get("report_schema") != 1:
            raise ValueError("report schema mismatch")
        for key in ("fixture_sha256", "source_sha256", "bend_commit"):
            if report.get(key) != first.get(key):
                raise ValueError(f"reports use different {key}")
        if report["fixture_sha256"] != sha(ROOT / "fixture.json") or report["source_sha256"] != source_hash():
            raise ValueError("reports do not match this checkout's fixture/source")
        if report["bend_commit"] != PIN["bend_commit"]:
            raise ValueError("reports do not use the pinned Bend commit")
        compare_results(report["reference"], report["result"], fixture)
    return [compare_results(first["result"], report["result"], fixture) for report in reports[1:]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--out", default=str(ROOT / ".build/prepared"))
    p = sub.add_parser("run")
    p.add_argument("--backend", choices=("cpu", "metal"), required=True)
    p.add_argument("--bend-source", required=True)
    p.add_argument("--bun", default="bun")
    p.add_argument("--bend-command", help="explicit compiler adapter command; recorded in report")
    p.add_argument("--cargo", default="cargo")
    p.add_argument("--rustc", default="rustc")
    p.add_argument("--reference", help="reference executable when CARGO_TARGET_DIR is overridden")
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--repetitions", type=int, default=3)
    p.add_argument("--verdict", action="store_true", help="require Lean-backed BendTT verdict")
    p.add_argument("--out", required=True)
    p = sub.add_parser("compare")
    p.add_argument("reports", nargs="+")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            print(prepare(args.out))
        elif args.action == "run":
            run(args)
        else:
            print(json.dumps(compare_reports(args.reports), indent=2))
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"FAILED: {exc}\n")


if __name__ == "__main__":
    main()
