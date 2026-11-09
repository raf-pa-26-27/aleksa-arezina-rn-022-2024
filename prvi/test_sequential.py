from unittest.mock import patch

import numpy as np

from model import Candidate
from pareto import (
    dominates,
    non_dominated_sort,
    calculate_crowding_distance,
    update_population_and_archive,
)
from search import (
    mutate_sequence,
    generate_offspring,
    initialize_search,
    run_sequential,
)
from metrics import (
    calculate_score_ranges,
    calculate_hypervolume,
)


AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
INITIAL_SEQUENCE = "TEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTN"

def test_pareto():
    assert dominates((5, 4, 3), (3, 4, 3))
    assert not dominates((5, 4, 3), (6, 2, 3))
    assert not dominates((5, 4, 3), (5, 4, 3))
    assert not dominates((3, 4, 3), (5, 4, 3))

    print("All dominance checks passed.")

    a = Candidate("AAAA", (5, 4, 3))
    b = Candidate("CCCC", (3, 4, 3))
    c = Candidate("DDDD", (6, 2, 3))
    d = Candidate("EEEE", (2, 2, 1))
    e = Candidate("FFFF", (5, 4, 3))

    candidates = [a, b, c, d, e]
    fronts = non_dominated_sort(candidates)

    assert len(fronts) == 3

    assert fronts[0] == [a, c, e]
    assert fronts[1] == [b]
    assert fronts[2] == [d]

    assert [candidate.rank for candidate in candidates] == [1, 2, 1, 3, 1]

    # Ulazna lista i dalje sadrži sve kandidate.
    assert len(candidates) == 5

    print("All ranking checks passed.")

    a = Candidate("AAAA", (1, 10, 5))
    b = Candidate("CCCC", (4, 7, 5))
    c = Candidate("DDDD", (5, 6, 5))
    d = Candidate("EEEE", (10, 1, 5))

    calculate_crowding_distance([a, b, c, d])

    assert a.crowding_distance == float("inf")
    assert d.crowding_distance == float("inf")

    assert abs(b.crowding_distance - 8 / 9) < 1e-9
    assert abs(c.crowding_distance - 12 / 9) < 1e-9

    print("All crowding distance checks passed.")

    a = Candidate("AAAA", (1, 10, 5))
    b = Candidate("CCCC", (4, 7, 5))
    c = Candidate("DDDD", (5, 6, 5))
    d = Candidate("EEEE", (10, 1, 5))
    e = Candidate("FFFF", (0, 0, 0))

    new_population, new_archive = update_population_and_archive(
        population=[a, b, e],
        archive=[a, b],
        new_candidates=[c, d],
        population_size=3,
    )

    # A i B se pojavljuju u dvije ulazne liste,
    # ali u rezultatu smiju biti samo jednom.
    assert len(new_archive) == 4
    assert {candidate.sequence for candidate in new_archive} == {
        "AAAA", "CCCC", "DDDD", "EEEE",
    }

    # Krajnji kandidati A i D imaju beskonačnu razređenost.
    # C ima veću razređenost od B.
    assert len(new_population) == 3
    assert {candidate.sequence for candidate in new_population} == {
        "AAAA", "DDDD", "EEEE",
    }

    # Dominirani kandidat E ne pripada arhivi.
    assert e.rank == 2

    print("All population update checks passed.")

    larger_population, _ = update_population_and_archive(
    population=[a, b, e],
    archive=[a, b],
    new_candidates=[c, d],
    population_size=5,
    )

    assert len(larger_population) == 5
    assert e in larger_population
    print("Extra population update check passed.")

def test_search():
    rng = np.random.default_rng(42)
    original = "TEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTN"

    mutated = mutate_sequence(original, 3, rng)

    assert len(mutated) == len(original)
    assert set(mutated) <= set(AMINO_ACIDS)
    assert sum(a != b for a, b in zip(original, mutated)) == 3

    assert mutate_sequence(original, 0, rng) == original

    fully_mutated = mutate_sequence(original, 100, rng)
    assert sum(
        a != b for a, b in zip(original, fully_mutated)
    ) == len(original)

    print("All mutation checks passed.")

    parents = [
        Candidate("AAAAAAAA"),
        Candidate("CCCCCCCC"),
    ]

    for _ in range(20):
        child = generate_offspring(parents, 0.0, rng)

        assert len(child) == 8
        assert set(child) == {"A", "C"}

        changes = sum(
            child[i] != child[i - 1]
            for i in range(1, len(child))
        )
        assert changes == 1

    print("All crossover checks passed.")

    for _ in range(20):
        child = generate_offspring(parents, 2.0, rng)

        assert len(child) == 8
        assert set(child) <= set(AMINO_ACIDS)

    print("All offspring checks passed.")

    population, archive, records, stopped = initialize_search(
        initial_sequence=original,
        population_size=10,
        max_initial_mutations=3,
        budget=20,
        rng=rng,
    )

    assert not stopped
    assert len(records) == 10
    assert len(population) == 10
    assert original in records
    assert archive

    assert all(candidate.scores is not None for candidate in records.values())

    # Svaki početni mutant razlikuje se od originala na 1–3 pozicije.
    for sequence in records:
        if sequence != original:
            changes = sum(a != b for a, b in zip(original, sequence))
            assert 1 <= changes <= 3

    # Budžet manji od N ograničava i početnu populaciju.
    small_population, _, small_records, _ = initialize_search(
        initial_sequence=original,
        population_size=10,
        max_initial_mutations=3,
        budget=4,
        rng=rng,
    )

    assert len(small_records) == 4
    assert len(small_population) == 4

    print("All initialization checks passed.")

    result = run_sequential(
        initial_sequence=original,
        population_size=10,
        max_initial_mutations=3,
        batch_size=5,
        mutation_lambda=2.0,
        budget=23,
        rng=rng,
    )
    archive = result.archive
    records = result.records
    reason = result.stop_reason

    assert reason == "budget_exhausted"
    assert len(records) == 23
    assert archive
    assert all(candidate.scores is not None for candidate in records.values())
    assert result.stats.evaluations_started == 23
    assert result.stats.evaluations_completed == 23

    # Arhiva mora biti prvi sloj među svim evaluiranim kandidatima.
    expected_front = non_dominated_sort(list(records.values()))[0]

    assert {candidate.sequence for candidate in archive} == {
        candidate.sequence for candidate in expected_front
    }

    print("All sequential search checks passed.")

    small_result = run_sequential(
        initial_sequence=original,
        population_size=10,
        max_initial_mutations=3,
        batch_size=5,
        mutation_lambda=2.0,
        budget=4,
        rng=rng,
    )
    small_records = small_result.records
    small_reason = small_result.stop_reason

    assert len(small_records) == 4
    assert small_reason == "budget_exhausted"
    print("Extra sequential check passed.")

    try:
        run_sequential(
            initial_sequence=original,
            population_size=10,
            max_initial_mutations=3,
            batch_size=0,
            mutation_lambda=2.0,
            budget=23,
            rng=rng,
        )
    except ValueError as error:
        assert str(error) == "batch_size must be at least 1"
    else:
        raise AssertionError("Expected ValueError for batch_size=0")
    print("Validation check passed.")



def test_metrics():
    from math import isclose

    assert calculate_hypervolume([]) == 0.0

    best = Candidate("AAAA", (10.0, 10.0, 10.0))
    weaker = Candidate("CCCC", (5.0, 5.0, 5.0))

    assert isclose(
        calculate_hypervolume([best]),
        1.1 ** 3,
    )

    # Dominirana tačka ne dodaje novu zapreminu.
    assert isclose(
        calculate_hypervolume([best, weaker]),
        calculate_hypervolume([best]),
    )

    # Dvije različite kompromisne tačke i njihovo preklapanje.
    first = Candidate("DDDD", (10.0, 0.0, 0.0))
    second = Candidate("EEEE", (0.0, 10.0, 0.0))

    assert isclose(
        calculate_hypervolume([first, second]),
        0.021,
    )

    print("All metric checks passed.")

def test_duplicate_limit():
    rng = np.random.default_rng(42)

    with patch(
        "search.generate_offspring",
        return_value=INITIAL_SEQUENCE,
    ) as mocked_generator:
        result = run_sequential(
            initial_sequence=INITIAL_SEQUENCE,
            population_size=4,
            max_initial_mutations=3,
            batch_size=5,
            mutation_lambda=2.0,
            budget=20,
            rng=rng,
        )

    assert result.stop_reason == "duplicate_limit"
    assert len(result.records) == 4
    assert result.stats.evaluations_started == 4
    assert result.stats.evaluations_completed == 4
    assert mocked_generator.call_count == 100

def test_budget_one():
    result = run_sequential(
        initial_sequence=INITIAL_SEQUENCE,
        population_size=10,
        max_initial_mutations=3,
        batch_size=5,
        mutation_lambda=2.0,
        budget=1,
        rng=np.random.default_rng(42),
    )

    assert result.stop_reason == "budget_exhausted"
    assert len(result.records) == 1
    assert result.archive[0].sequence == INITIAL_SEQUENCE
    assert result.stats.evaluations_started == 1
    assert result.stats.evaluations_completed == 1
    assert result.iteration_seconds == []

def test_partial_iteration_duplicate_limit():
    new_sequence = "W" * len(INITIAL_SEQUENCE)

    generated_sequences = (
        [new_sequence] + [INITIAL_SEQUENCE] * 100
    )

    with patch(
        "search.generate_offspring",
        side_effect=generated_sequences,
    ) as mocked_generator:
        result = run_sequential(
            initial_sequence=INITIAL_SEQUENCE,
            population_size=4,
            max_initial_mutations=3,
            batch_size=5,
            mutation_lambda=2.0,
            budget=20,
            rng=np.random.default_rng(42),
        )

    assert result.stop_reason == "duplicate_limit"
    assert len(result.records) == 5
    assert result.records[new_sequence].scores is not None
    assert result.stats.evaluations_started == 5
    assert result.stats.evaluations_completed == 5
    assert len(result.iteration_seconds) == 1
    assert mocked_generator.call_count == 101

    expected_front = non_dominated_sort(
        list(result.records.values())
    )[0]

    assert {candidate.sequence for candidate in result.archive} == {
        candidate.sequence for candidate in expected_front
    }

def test_reproducibility():
    parameters = {
        "initial_sequence": INITIAL_SEQUENCE,
        "population_size": 10,
        "max_initial_mutations": 3,
        "batch_size": 5,
        "mutation_lambda": 2.0,
        "budget": 53,
    }

    first = run_sequential(
        **parameters,
        rng=np.random.default_rng(42),
    )

    second = run_sequential(
        **parameters,
        rng=np.random.default_rng(42),
    )

    first_records = {
        sequence: candidate.scores
        for sequence, candidate in first.records.items()
    }
    second_records = {
        sequence: candidate.scores
        for sequence, candidate in second.records.items()
    }

    assert first_records == second_records
    assert list(first.records) == list(second.records)

    assert {
        candidate.sequence for candidate in first.archive
    } == {
        candidate.sequence for candidate in second.archive
    }

    assert first.stop_reason == second.stop_reason
    assert (
        first.stats.evaluations_started
        == second.stats.evaluations_started
    )
    assert (
        first.stats.evaluations_completed
        == second.stats.evaluations_completed
    )
    assert len(first.iteration_seconds) == len(second.iteration_seconds)

if __name__ == "__main__":
    test_pareto()
    test_search()
    test_metrics()
    test_duplicate_limit()
    test_budget_one()
    test_partial_iteration_duplicate_limit()
    test_reproducibility()

    print("All sequential checks passed.")