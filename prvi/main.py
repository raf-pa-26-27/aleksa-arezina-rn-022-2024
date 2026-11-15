import argparse
from dataclasses import asdict
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

import numpy as np

from model import ProcessResult, SearchStats
from search import run_sequential, run_threaded, validate_parameters
from processes import TOPOLOGIES, run_multiprocess
from metrics import calculate_score_ranges, calculate_hypervolume


INITIAL_SEQUENCE = "TEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTN"


def serialize_archive(archive):
    return [
        {"sequence": candidate.sequence, "scores": candidate.scores,
         "origin_process": candidate.origin_process}
        for candidate in archive
    ]


def summarize_process(result):
    iterations = result.iteration_seconds
    return {
        "process_id": result.process_id,
        "mutation_lambda": result.mutation_lambda,
        "budget": result.budget,
        "stop_reason": result.stop_reason,
        "total_seconds": (
            result.lifecycle_seconds if result.lifecycle_seconds is not None
            else result.total_seconds
        ),
        "search_seconds": result.total_seconds,
        "iteration_count": len(iterations),
        "iteration_seconds": iterations,
        "average_iteration_seconds": sum(iterations) / len(iterations) if iterations else 0.0,
        **asdict(result.stats),
        "records_count": result.records_count,
        "foreign_archive_size": sum(
            candidate.origin_process != result.process_id for candidate in result.archive
        ),
        "archive_size": len(result.archive),
        "score_ranges": calculate_score_ranges(result.archive),
        "hypervolume": calculate_hypervolume(result.archive),
        "archive": serialize_archive(result.archive),
    }


def main():
    parser = argparse.ArgumentParser(description="Multi-objective sequence optimization")
    parser.add_argument("--population-size", type=int, required=True)
    parser.add_argument("--max-initial-mutations", type=int, required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    mutation = parser.add_mutually_exclusive_group(required=True)
    mutation.add_argument("--mutation-lambda", type=float)
    mutation.add_argument("--mutation-lambdas", type=float, nargs="+")
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument("--migration-count", type=int, default=5, help="K: candidates per neighbor")
    parser.add_argument("--exchange-interval", type=int, default=5, help="E: completed local iterations")
    parser.add_argument("--topology", choices=TOPOLOGIES, default="ring")
    parser.add_argument("--timeout", type=float, default=300.0, help="multiprocess safety timeout in seconds")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    lambdas = (
        args.mutation_lambdas if args.mutation_lambdas is not None
        else [args.mutation_lambda] * args.processes
    )
    try:
        if args.seed < 0:
            raise ValueError("seed must be non-negative")
        if args.threads < 1 or args.processes < 1:
            raise ValueError("threads and processes must be at least 1")
        if args.budget < args.processes or args.budget % args.processes:
            raise ValueError("budget must be >= processes and divisible by processes")
        if len(lambdas) != args.processes:
            raise ValueError("provide exactly one mutation lambda per process")
        if args.migration_count < 0 or args.exchange_interval < 1:
            raise ValueError("migration-count must be >= 0 and exchange-interval >= 1")
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        for value in lambdas:
            validate_parameters(
                INITIAL_SEQUENCE, args.population_size, args.max_initial_mutations,
                args.batch_size, value, args.budget // args.processes,
            )
    except ValueError as error:
        parser.error(str(error))

    parameters = {
        "population_size": args.population_size,
        "max_initial_mutations": args.max_initial_mutations,
        "batch_size": args.batch_size,
        "mutation_lambda": lambdas[0],
        "budget": args.budget,
    }
    start = perf_counter()
    failures = {}

    if args.processes > 1:
        combined = run_multiprocess(
            initial_sequence=INITIAL_SEQUENCE,
            **parameters,
            seed=args.seed,
            process_count=args.processes,
            thread_count=args.threads,
            migration_count=args.migration_count,
            exchange_interval=args.exchange_interval,
            topology=args.topology,
            process_lambdas=lambdas,
            timeout=args.timeout,
        )
        archive = combined.archive
        local_results = combined.process_results
        failures = combined.failures
    else:
        rng = np.random.default_rng(args.seed)
        if args.threads == 1:
            search_result = run_sequential(INITIAL_SEQUENCE, rng=rng, **parameters)
        else:
            search_result = run_threaded(
                INITIAL_SEQUENCE, rng=rng, thread_count=args.threads, **parameters
            )
        archive = search_result.archive
        local_results = [ProcessResult(
            process_id=0,
            mutation_lambda=lambdas[0],
            budget=args.budget,
            archive=archive,
            stats=search_result.stats,
            stop_reason=search_result.stop_reason,
            iteration_seconds=search_result.iteration_seconds,
            total_seconds=perf_counter() - start,
            records_count=len(search_result.records),
        )]

    total_seconds = perf_counter() - start
    totals = {
        name: sum(getattr(local.stats, name) for local in local_results)
        for name in asdict(SearchStats())
    }
    iteration_seconds = [
        seconds for local in local_results for seconds in local.iteration_seconds
    ]
    stop_reason = (
        "process_error" if failures else
        "budget_exhausted" if totals["evaluations_started"] == args.budget else
        "duplicate_limit"
    )
    result = {
        "initial_sequence": INITIAL_SEQUENCE,
        "seed": args.seed,
        "seed_strategy": "SeedSequence(seed).spawn(P)" if args.processes > 1 else "default_rng(seed)",
        "parameters": {
            **parameters,
            "mutation_lambdas": lambdas,
            "threads": args.threads,
            "processes": args.processes,
            "migration_count": args.migration_count,
            "exchange_interval": args.exchange_interval,
            "topology": args.topology,
            "timeout": args.timeout,
        },
        "stop_reason": stop_reason,
        "total_seconds": total_seconds,
        "evaluations": totals["evaluations_completed"],
        **totals,
        "evaluation_counts_complete": len(local_results) == args.processes,
        "failed_processes": failures,
        "archive_size": len(archive),
        "archive": serialize_archive(archive),
        "iteration_count": len(iteration_seconds),
        "iteration_seconds": iteration_seconds,
        "average_iteration_seconds": (
            sum(iteration_seconds) / len(iteration_seconds) if iteration_seconds else 0.0
        ),
        "score_ranges": calculate_score_ranges(archive),
        "hypervolume": calculate_hypervolume(archive),
        "hypervolume_bounds": {"lower": [0, 0, 0], "upper": [10, 10, 10], "reference": [-0.1] * 3},
        "process_results": [summarize_process(local) for local in local_results],
        "environment": {
            "python": sys.version,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cores": os.cpu_count(),
            "gil_enabled": sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else None,
        },
    }
    if args.output is None:
        args.output = (
            Path(__file__).resolve().parent / "results"
            / f"processes{args.processes}_threads{args.threads}_seed{args.seed}.json"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        json.dump(result, file, indent=2, allow_nan=False)

    print(f"Stop reason: {stop_reason}")
    print(f"Evaluations: {result['evaluations']} (started: {result['evaluations_started']})")
    print(f"Archive size: {len(archive)}")
    print(f"Total time: {total_seconds:.4f} seconds")
    print(f"Local iterations: {len(iteration_seconds)}")
    print(f"Average local iteration time: {result['average_iteration_seconds']:.6f} seconds")
    print(f"Hypervolume: {result['hypervolume']:.6f}")
    for local in local_results:
        print(
            f"Process {local.process_id}: evals={local.stats.evaluations_completed}, "
            f"sent={local.stats.candidates_sent}, received={local.stats.candidates_received}, "
            f"accepted={local.stats.candidates_accepted}, stop={local.stop_reason}"
        )
    for process_id, error in failures.items():
        print(f"Process {process_id} failed: {error}", file=sys.stderr)
    print(f"Results saved to: {args.output.resolve()}")
    return 1 if failures else 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
