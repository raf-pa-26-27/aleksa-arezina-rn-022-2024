import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from search import run_sequential, validate_parameters
from metrics import calculate_score_ranges, calculate_hypervolume


INITIAL_SEQUENCE = "TEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTN"


def main():
    parser = argparse.ArgumentParser(
        description="Sequential multi-objective sequence optimization"
    )

    parser.add_argument("--population-size", type=int, required=True)
    parser.add_argument("--max-initial-mutations", type=int, required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--mutation-lambda", type=float, required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "results" / "sequential.json",
    )

    args = parser.parse_args()

    parameters = {
        "population_size": args.population_size,
        "max_initial_mutations": args.max_initial_mutations,
        "batch_size": args.batch_size,
        "mutation_lambda": args.mutation_lambda,
        "budget": args.budget,
    }

    try:
        validate_parameters(INITIAL_SEQUENCE, **parameters)

        if args.seed < 0:
            raise ValueError("seed must be non-negative")
    except ValueError as error:
        parser.error(str(error))

    rng = np.random.default_rng(args.seed)

    start = perf_counter()

    search_result = run_sequential(
        initial_sequence=INITIAL_SEQUENCE,
        rng=rng,
        **parameters,
    )

    total_seconds = perf_counter() - start

    archive = search_result.archive
    records = search_result.records
    stop_reason = search_result.stop_reason

    iteration_count = len(search_result.iteration_seconds)
    average_iteration_seconds = (
        sum(search_result.iteration_seconds) / iteration_count
        if iteration_count > 0
        else 0.0
    )
    hypervolume = calculate_hypervolume(archive)

    result = {
        "initial_sequence": INITIAL_SEQUENCE,
        "seed": args.seed,
        "parameters": parameters,
        "stop_reason": stop_reason,
        "total_seconds": total_seconds,
        "evaluations": search_result.stats.evaluations_completed,
        "evaluations_started": search_result.stats.evaluations_started,
        "archive_size": len(archive),
        "archive": [
            {
                "sequence": candidate.sequence,
                "scores": candidate.scores,
            }
            for candidate in archive
        ],
        "iteration_count": iteration_count,
        "iteration_seconds": search_result.iteration_seconds,
        "average_iteration_seconds": average_iteration_seconds,
        "score_ranges": calculate_score_ranges(archive),
        "hypervolume": hypervolume,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with args.output.open("w", encoding="utf-8") as file:
        json.dump(result, file, indent=2)

    print(f"Stop reason: {stop_reason}")
    print(f"Evaluations: {search_result.stats.evaluations_completed}")
    print(f"Archive size: {len(archive)}")
    print(f"Total time: {total_seconds:.4f} seconds")
    print(f"Results saved to: {args.output.resolve()}")
    print(f"Iterations: {iteration_count}")
    print(f"Average iteration time: {average_iteration_seconds:.6f} seconds")
    print(f"Hypervolume: {hypervolume:.6f}")


if __name__ == "__main__":
    main()