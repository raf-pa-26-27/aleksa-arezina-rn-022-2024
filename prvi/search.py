import numpy as np
import math
from time import perf_counter
from model import Candidate, SearchResult, SearchStats
from oceni import oceni
from pareto import update_population_and_archive, non_dominated_sort



AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"

def validate_parameters(
    initial_sequence: str,
    population_size: int,
    max_initial_mutations: int,
    batch_size: int,
    mutation_lambda: float,
    budget: int,
) -> None:
    if not isinstance(initial_sequence, str):
        raise ValueError("initial_sequence must be a string")

    if len(initial_sequence) < 2:
        raise ValueError("initial_sequence must contain at least 2 letters")

    if not set(initial_sequence) <= set(AMINO_ACIDS):
        raise ValueError("initial_sequence contains invalid amino acids")

    integer_parameters = {
        "population_size": population_size,
        "max_initial_mutations": max_initial_mutations,
        "batch_size": batch_size,
        "budget": budget,
    }

    for name, value in integer_parameters.items():
        if type(value) is not int:
            raise ValueError(f"{name} must be an integer")

    if population_size < 2:
        raise ValueError("population_size must be at least 2")

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    if budget < 1:
        raise ValueError("budget must be at least 1")

    if not 1 <= max_initial_mutations <= len(initial_sequence):
        raise ValueError(
            "max_initial_mutations must be between 1 and sequence length"
        )

    if (
        type(mutation_lambda) not in (int, float)
        or not math.isfinite(mutation_lambda)
        or mutation_lambda < 0
    ):
        raise ValueError("mutation_lambda must be finite and non-negative")

def mutate_sequence(
    sequence: str,
    mutation_count: int,
    rng: np.random.Generator,
) -> str:
    letters = list(sequence)
    mutation_count = min(mutation_count, len(sequence))
    positions = rng.choice(
        len(sequence),
        size=mutation_count,
        replace=False,
    )

    for position in positions:
        alternatives = [
            amino_acid
            for amino_acid in AMINO_ACIDS
            if amino_acid != letters[position]
        ]
        letters[position] = rng.choice(alternatives)

    return "".join(letters)

def generate_offspring(
    population: list[Candidate],
    mutation_lambda: float,
    rng: np.random.Generator,
) -> str:
    parent_indexes = rng.choice(
        len(population),
        size=2,
        replace=False,
    )
    first_parent = population[parent_indexes[0]].sequence
    second_parent = population[parent_indexes[1]].sequence
    cut = rng.integers(1, len(first_parent))
    child = first_parent[:cut] + second_parent[cut:]
    mutation_count = int(rng.poisson(mutation_lambda))
    return mutate_sequence(child, mutation_count, rng)

def initialize_search(
    initial_sequence: str,
    population_size: int,
    max_initial_mutations: int,
    budget: int,
    rng: np.random.Generator,
    stats: SearchStats | None = None,
) -> tuple[list[Candidate], list[Candidate], dict[str, Candidate], bool]:   
    if stats is None:
        stats = SearchStats()
    records: dict[str, Candidate] = {}
    initial_candidate = Candidate(initial_sequence)
    records[initial_sequence] = initial_candidate
    stats.evaluations_started += 1
    initial_candidate.scores = tuple(oceni(initial_sequence))
    stats.evaluations_completed += 1
    failed_attempts = 0
    target_size = min(population_size, budget)

    while len(records) < target_size and failed_attempts < 100:
        mutation_count = int(rng.integers(1, max_initial_mutations + 1))
        new_sequence = mutate_sequence(initial_sequence, mutation_count, rng)
        if new_sequence in records:
            failed_attempts += 1
            continue
        failed_attempts = 0
        new_candidate = Candidate(new_sequence)
        records[new_sequence] = new_candidate
        stats.evaluations_started += 1
        new_candidate.scores = tuple(oceni(new_sequence))
        stats.evaluations_completed += 1

    population, archive = update_population_and_archive(
        population=[],
        archive=[],
        new_candidates=list(records.values()),
        population_size=population_size,
    )

    stopped_by_duplicates = failed_attempts >= 100

    return population, archive, records, stopped_by_duplicates


def run_sequential(
    initial_sequence: str,
    population_size: int,
    max_initial_mutations: int,
    batch_size: int,
    mutation_lambda: float,
    budget: int,
    rng: np.random.Generator,
) -> SearchResult:
    iteration_seconds: list[float] = []
    initial_sequence = initial_sequence.upper().strip()
    validate_parameters(
        initial_sequence,
        population_size,
        max_initial_mutations,
        batch_size,
        mutation_lambda,
        budget,
    )
    stats = SearchStats()
    population, archive, records, stopped = initialize_search(initial_sequence, population_size, max_initial_mutations, budget, rng, stats=stats)
  
    if stopped:
        return SearchResult(
            archive=archive,
            records=records,
            stop_reason="duplicate_limit",
            iteration_seconds=iteration_seconds,
            stats=stats,
        )
    failed_attempts = 0

    while stats.evaluations_started < budget and failed_attempts < 100:
        iteration_start = perf_counter()
        new_candidates: list[Candidate] = []

        while len(new_candidates) < batch_size and stats.evaluations_started < budget and failed_attempts < 100:
            new_sequence = generate_offspring(population, mutation_lambda, rng)
            if new_sequence in records:
                failed_attempts += 1
                continue
            failed_attempts = 0
            new_candidate = Candidate(new_sequence)
            records[new_sequence] = new_candidate
            stats.evaluations_started += 1
            new_candidate.scores = tuple(oceni(new_sequence))
            stats.evaluations_completed += 1
            new_candidates.append(new_candidate)
  
        population, archive = update_population_and_archive(population, archive, new_candidates, population_size)
        iteration_seconds.append(perf_counter() - iteration_start)
    
    stop_reason = (
        "duplicate_limit"
        if failed_attempts >= 100
        else "budget_exhausted"
    )
    return SearchResult(
        archive=archive,
        records=records,
        stop_reason=stop_reason,
        iteration_seconds=iteration_seconds,
        stats=stats,
    )
        
