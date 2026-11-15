"""Run the six assignment configurations with the same three seeds."""

import argparse
import json
import math
from pathlib import Path
from statistics import mean, stdev
import subprocess
import sys
from time import perf_counter


def write_run_markdown(result, path, label):
    lines = [
        f"# {label}, seed {result['seed']}", "",
        "## Parameters", "", "```json",
        json.dumps(result["parameters"], indent=2), "```", "",
        f"Stop: {result['stop_reason']}",
        f"Time: {result['total_seconds']:.6f} s",
        f"Evaluations: {result['evaluations']} (started {result['evaluations_started']})",
        f"Archive size: {result['archive_size']}",
        f"Hypervolume: {result['hypervolume']:.9f}", "",
        "## Processes", "",
        "| Process | Lambda | Budget | Evaluations | Sent | Received | Accepted | Foreign archive points | Time s | Mean iteration s |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for local in result["process_results"]:
        lines.append(
            f"| {local['process_id']} | {local['mutation_lambda']} | {local['budget']} | "
            f"{local['evaluations_completed']} | {local['candidates_sent']} | "
            f"{local['candidates_received']} | {local['candidates_accepted']} | "
            f"{local['foreign_archive_size']} | {local['total_seconds']:.6f} | "
            f"{local['average_iteration_seconds']:.6f} |"
        )
    lines += [
        "", "## Final archive", "",
        "| Sequence | Score 1 | Score 2 | Score 3 | Origin process |",
        "|---|---:|---:|---:|---:|",
    ]
    for candidate in result["archive"]:
        a, b, c = candidate["scores"]
        lines.append(f"| {candidate['sequence']} | {a} | {b} | {c} | {candidate['origin_process']} |")
    lines += ["", f"Score ranges: {result['score_ranges']}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--population-size", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-initial-mutations", type=int, default=3)
    parser.add_argument("--mutation-lambda", type=float, default=2.0)
    parser.add_argument("--process-lambdas", type=float, nargs=4, default=[0.5, 1.0, 2.0, 4.0])
    parser.add_argument("--seeds", type=int, nargs=3, default=[42, 123, 2026])
    parser.add_argument("--threads", type=int, default=4, help="threads in P2")
    parser.add_argument("--migration-count", type=int, default=5)
    parser.add_argument("--exchange-interval", type=int, default=5)
    parser.add_argument("--alternative-exchange-interval", type=int, default=1)
    parser.add_argument("--second-topology", choices=["star", "all-to-all"], default="all-to-all")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--configurations", choices=[f"P{i}" for i in range(1, 7)], nargs="+",
                        default=[f"P{i}" for i in range(1, 7)])
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "results" / "experiments")
    args = parser.parse_args()
    if args.budget < 4 or args.budget % 4:
        parser.error("budget must be >= 4 and divisible by 4")
    if args.population_size < 2 or args.batch_size < 1:
        parser.error("population-size must be >= 2 and batch-size >= 1")
    if not 1 <= args.max_initial_mutations <= 32:
        parser.error("max-initial-mutations must be between 1 and 32")
    if args.migration_count < 0 or min(args.exchange_interval, args.alternative_exchange_interval) < 1:
        parser.error("migration-count must be >= 0 and exchange intervals >= 1")
    if any(not math.isfinite(value) or value < 0 for value in [args.mutation_lambda, *args.process_lambdas]):
        parser.error("mutation lambdas must be finite and non-negative")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    if args.threads < 4:
        parser.error("P2 requires at least 4 threads")
    if len(set(args.seeds)) != 3 or any(seed < 0 for seed in args.seeds):
        parser.error("provide three different non-negative seeds")
    if args.alternative_exchange_interval == args.exchange_interval:
        parser.error("P6 exchange interval must differ from P3-P5")
    if len(set(args.process_lambdas)) < 2:
        parser.error("P4-P6 require different lambdas across processes")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    script_dir = Path(__file__).resolve().parent
    entries = []
    results = []
    for label in args.configurations:
        processes = 1 if label in ("P1", "P2") else 4
        threads = args.threads if label == "P2" else 1
        topology = args.second_topology if label == "P5" else "ring"
        interval = args.alternative_exchange_interval if label == "P6" else args.exchange_interval
        for seed in args.seeds:
            name = f"{label}_seed{seed}"
            result_path = output_dir / f"{name}.json"
            command = [
                sys.executable, str(script_dir / "main.py"),
                "--population-size", str(args.population_size),
                "--batch-size", str(args.batch_size),
                "--max-initial-mutations", str(args.max_initial_mutations),
                "--budget", str(args.budget), "--seed", str(seed),
                "--threads", str(threads), "--processes", str(processes),
                "--migration-count", str(args.migration_count),
                "--exchange-interval", str(interval), "--topology", topology,
                "--timeout", str(args.timeout), "--output", str(result_path),
            ]
            if label in ("P4", "P5", "P6"):
                command += ["--mutation-lambdas", *map(str, args.process_lambdas)]
            else:
                command += ["--mutation-lambda", str(args.mutation_lambda)]
            print(f"Running {name} ...", flush=True)
            start = perf_counter()
            try:
                completed = subprocess.run(
                    command, cwd=script_dir, capture_output=True, text=True,
                    timeout=args.timeout + 15,
                )
                log = completed.stdout + completed.stderr
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                log = "Experiment exceeded the launcher timeout.\n"
                exit_code = -1
            (output_dir / f"{name}.log").write_text(log, encoding="utf-8")
            entry = {
                "configuration": label, "seed": seed, "exit_code": exit_code,
                "command": command, "launcher_seconds": perf_counter() - start,
                "result_file": result_path.name,
            }
            entries.append(entry)
            if exit_code == 0:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                results.append((label, result))
                write_run_markdown(result, output_dir / f"{name}.md", label)

    manifest = {
        "warmup": False,
        "repetitions_per_seed": 1,
        "runs": entries,
    }
    (output_dir / "runs.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    lines = [
        "# Experiment results", "",
        "Measured times exclude root interpreter startup and output I/O; child startup is included.",
        "No warm-up. One run per seed. Launcher duration is stored separately in runs.json.", "",
        "| Configuration | Seed | P | T | Time s | Evaluations | Archive | Hypervolume | Sent | Received |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, result in results:
        p = result["parameters"]
        lines.append(
            f"| {label} | {result['seed']} | {p['processes']} | {p['threads']} | "
            f"{result['total_seconds']:.6f} | {result['evaluations']} | {result['archive_size']} | "
            f"{result['hypervolume']:.9f} | {result['candidates_sent']} | {result['candidates_received']} |"
        )
    lines += ["", "## Means across seeds", "",
              "| Configuration | Successful runs | Mean time s | Time standard deviation | Mean hypervolume |",
              "|---|---:|---:|---:|---:|"]
    for label in args.configurations:
        group = [result for configuration, result in results if configuration == label]
        if group:
            times = [result["total_seconds"] for result in group]
            deviation = stdev(times) if len(times) > 1 else 0.0
            lines.append(
                f"| {label} | {len(group)} | {mean(times):.6f} | {deviation:.6f} | "
                f"{mean(result['hypervolume'] for result in group):.9f} |"
            )
    lines += ["", "Failures are recorded in runs.json and individual log files.",
              "This table contains measured data; interpretation belongs in the final report.", ""]
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    failures = sum(entry["exit_code"] != 0 for entry in entries)
    print(f"Finished {len(entries)} runs; failures: {failures}. Results: {output_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
