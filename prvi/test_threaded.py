from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import cycle, product
from threading import Event, Lock, current_thread
from time import sleep
import unittest
from unittest.mock import patch

import numpy as np

from model import Candidate, SearchStats
from oceni import oceni
from pareto import (
    non_dominated_sort,
    non_dominated_sort_parallel,
    update_population_and_archive,
)
from search import collect_candidates_threaded, run_sequential, run_threaded


INITIAL_SEQUENCE = "TEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTN"
PARAMETERS = dict(
    initial_sequence=INITIAL_SEQUENCE,
    population_size=10,
    max_initial_mutations=3,
    batch_size=5,
    mutation_lambda=2.0,
    budget=53,
)


def archive_sequences(candidates):
    return {candidate.sequence for candidate in candidates}


class ThreadedTests(unittest.TestCase):
    def test_parallel_ranks_match_sequential_with_ties(self):
        rng = np.random.default_rng(123)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for count in (0, 1, 2, 20, 60):
                scores = rng.integers(0, 6, size=(count, 3))
                sequential = [
                    Candidate(str(i), tuple(values))
                    for i, values in enumerate(scores)
                ]
                parallel = [
                    Candidate(candidate.sequence, candidate.scores)
                    for candidate in sequential
                ]
                expected = non_dominated_sort(sequential)
                actual = non_dominated_sort_parallel(parallel, pool)
                self.assertEqual(
                    [[candidate.sequence for candidate in front] for front in actual],
                    [[candidate.sequence for candidate in front] for front in expected],
                )
                self.assertEqual(
                    [candidate.rank for candidate in parallel],
                    [candidate.rank for candidate in sequential],
                )

    def test_thread_counts_match_sequential_result(self):
        expected = run_sequential(**PARAMETERS, rng=np.random.default_rng(42))
        for threads in (1, 4, 16):
            with self.subTest(threads=threads):
                actual = run_threaded(
                    **PARAMETERS,
                    rng=np.random.default_rng(42),
                    thread_count=threads,
                )
                self.assertEqual(actual.stop_reason, expected.stop_reason)
                self.assertEqual(list(actual.records), list(expected.records))
                self.assertEqual(
                    [candidate.scores for candidate in actual.records.values()],
                    [candidate.scores for candidate in expected.records.values()],
                )
                self.assertEqual(
                    archive_sequences(actual.archive), archive_sequences(expected.archive)
                )
                self.assertEqual(actual.stats.evaluations_started, 53)
                self.assertEqual(actual.stats.evaluations_completed, 53)
                self.assertEqual(len(actual.iteration_seconds), 9)

    def test_no_duplicate_evaluations_in_small_space(self):
        sequences = ["".join(letters) for letters in product("AC", repeat=3)]
        population = [Candidate("AAA", (1, 1, 1)), Candidate("CCC", (1, 1, 1))]
        records = {candidate.sequence: candidate for candidate in population}
        stats = SearchStats(2, 2)
        generated = cycle(sequences)
        evaluations = Counter()
        activity_lock = Lock()
        active = 0
        max_active = 0
        snapshot = [vars(candidate).copy() for candidate in population]

        def evaluate(sequence):
            nonlocal active, max_active
            with activity_lock:
                evaluations[sequence] += 1
                active += 1
                max_active = max(max_active, active)
            sleep(0.005)
            with activity_lock:
                active -= 1
            return (2.0, 2.0, 2.0)

        with (
            ThreadPoolExecutor(max_workers=16) as pool,
            patch("search.generate_offspring", side_effect=lambda *args: next(generated)),
            patch("search.oceni", side_effect=evaluate),
        ):
            new, failures = collect_candidates_threaded(
                population, records, stats, 50, 100, 2.0,
                np.random.default_rng(42), 0, pool, 16,
            )

        self.assertEqual(failures, 100)
        self.assertEqual(len(records), 8)
        self.assertEqual(len(new), 6)
        self.assertEqual(stats.evaluations_started, 8)
        self.assertEqual(stats.evaluations_completed, 8)
        self.assertTrue(all(count == 1 for count in evaluations.values()))
        self.assertTrue(all(candidate.scores is not None for candidate in new))
        self.assertEqual([vars(candidate).copy() for candidate in population], snapshot)
        self.assertGreater(max_active, 1)

    def test_reservations_respect_batch_and_remaining_budget(self):
        for batch_size, budget, expected in ((3, 50, 3), (10, 7, 5)):
            with self.subTest(batch_size=batch_size, budget=budget):
                population = [Candidate("AA", (1, 1, 1)), Candidate("CC", (1, 1, 1))]
                records = {candidate.sequence: candidate for candidate in population}
                stats = SearchStats(2, 2)
                generated = iter("".join(p) for p in product("ACDE", repeat=2))

                def evaluate(sequence):
                    sleep(0.002)
                    return (2.0, 2.0, 2.0)

                with (
                    ThreadPoolExecutor(max_workers=16) as pool,
                    patch("search.generate_offspring", side_effect=lambda *args: next(generated)),
                    patch("search.oceni", side_effect=evaluate),
                ):
                    new, _ = collect_candidates_threaded(
                        population, records, stats, batch_size, budget, 2.0,
                        np.random.default_rng(42), 0, pool, 16,
                    )

                self.assertEqual(len(new), expected)
                self.assertEqual(stats.evaluations_started, 2 + expected)
                self.assertEqual(stats.evaluations_completed, 2 + expected)
                self.assertTrue(all(records[c.sequence] is c for c in new))
                self.assertTrue(all(c.scores is not None for c in new))

    def test_partial_iteration_waits_and_updates_on_main_thread(self):
        new_sequence = "W" * len(INITIAL_SEQUENCE)
        supplied = [new_sequence] + [INITIAL_SEQUENCE] * 100
        batches = []

        def evaluate(sequence):
            if sequence == new_sequence:
                sleep(0.02)
            return oceni(sequence)

        def update(population, archive, new_candidates, population_size, **kwargs):
            self.assertEqual(current_thread().name, "MainThread")
            self.assertTrue(all(c.scores is not None for c in new_candidates))
            batches.append([c.sequence for c in new_candidates])
            return update_population_and_archive(
                population, archive, new_candidates, population_size, **kwargs
            )

        with (
            patch("search.generate_offspring", side_effect=supplied) as generated,
            patch("search.oceni", side_effect=evaluate),
            patch("search.update_population_and_archive", side_effect=update),
        ):
            result = run_threaded(
                **{**PARAMETERS, "population_size": 4, "budget": 20},
                rng=np.random.default_rng(42), thread_count=8,
            )

        self.assertEqual(generated.call_count, 101)
        self.assertEqual(result.stop_reason, "duplicate_limit")
        self.assertEqual(result.stats.evaluations_completed, 5)
        self.assertEqual(batches[-1], [new_sequence])
        self.assertEqual(len(result.iteration_seconds), 1)
        expected = non_dominated_sort(list(result.records.values()))[0]
        self.assertEqual(archive_sequences(result.archive), archive_sequences(expected))

    def test_finished_worker_takes_another_candidate_without_barrier(self):
        population = [Candidate("AA", (1, 1, 1)), Candidate("CC", (1, 1, 1))]
        records = {candidate.sequence: candidate for candidate in population}
        stats = SearchStats(2, 2)
        slow_started = Event()
        third_started = Event()
        supplied = iter(["AC", "CA", "DD"])

        def evaluate(sequence):
            if sequence == "AC":
                slow_started.set()
                if not third_started.wait(5):
                    raise RuntimeError("Fast worker waited for the slow worker")
            else:
                if not slow_started.wait(5):
                    raise RuntimeError("Slow evaluation did not start")
                if sequence == "DD":
                    third_started.set()
            return (2.0, 2.0, 2.0)

        with (
            ThreadPoolExecutor(max_workers=2) as pool,
            patch("search.generate_offspring", side_effect=lambda *args: next(supplied)),
            patch("search.oceni", side_effect=evaluate),
        ):
            new, _ = collect_candidates_threaded(
                population, records, stats, 3, 5, 2.0,
                np.random.default_rng(42), 0, pool, 2,
            )
        self.assertEqual(len(new), 3)
        self.assertTrue(third_started.is_set())

    def test_small_budget_and_validation(self):
        for budget in (1, 4, 10):
            result = run_threaded(
                **{**PARAMETERS, "budget": budget},
                rng=np.random.default_rng(42), thread_count=16,
            )
            self.assertEqual(result.stats.evaluations_started, budget)
            self.assertEqual(result.stats.evaluations_completed, budget)
            self.assertEqual(result.iteration_seconds, [])

        with self.assertRaises(ValueError):
            run_threaded(
                **PARAMETERS, rng=np.random.default_rng(42), thread_count=0
            )

    def test_duplicate_limit_during_initialization(self):
        with patch("search.mutate_sequence", return_value=INITIAL_SEQUENCE):
            result = run_threaded(
                **PARAMETERS, rng=np.random.default_rng(42), thread_count=4
            )
        self.assertEqual(result.stop_reason, "duplicate_limit")
        self.assertEqual(result.stats.evaluations_started, 1)
        self.assertEqual(result.stats.evaluations_completed, 1)
        self.assertEqual(result.iteration_seconds, [])

    def test_worker_exception_propagates_after_pending_work_finishes(self):
        population = [Candidate("AA", (1, 1, 1)), Candidate("CC", (1, 1, 1))]
        records = {candidate.sequence: candidate for candidate in population}
        stats = SearchStats(2, 2)
        supplied = iter(["AC", "CA"])
        slow_started = Event()
        slow_finished = Event()

        def evaluate(sequence):
            if sequence == "AC":
                slow_started.set()
                sleep(0.02)
                slow_finished.set()
                return (2.0, 2.0, 2.0)
            if not slow_started.wait(5):
                raise RuntimeError("Slow evaluation did not start")
            raise ValueError("Synthetic evaluator failure")

        with (
            ThreadPoolExecutor(max_workers=2) as pool,
            patch("search.generate_offspring", side_effect=lambda *args: next(supplied)),
            patch("search.oceni", side_effect=evaluate),
        ):
            with self.assertRaisesRegex(ValueError, "Synthetic evaluator failure"):
                collect_candidates_threaded(
                    population, records, stats, 2, 4, 2.0,
                    np.random.default_rng(42), 0, pool, 2,
                )
            self.assertTrue(slow_finished.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
