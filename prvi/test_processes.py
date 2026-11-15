from collections import Counter
import multiprocessing as mp
import os
from queue import Queue
from time import perf_counter, sleep
import unittest
from unittest.mock import patch

import numpy as np

from model import Candidate, SearchStats
from pareto import non_dominated_sort, update_population_and_archive
from processes import (
    _process_entry, get_neighbors, merge_archives, receive_candidates,
    run_multiprocess, select_migrants, send_candidates,
)
from search import run_threaded


INITIAL_SEQUENCE = "TEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTN"
PARAMETERS = dict(
    initial_sequence=INITIAL_SEQUENCE,
    population_size=4,
    max_initial_mutations=2,
    batch_size=3,
    mutation_lambda=2.0,
    budget=84,
    seed=42,
    process_count=2,
    thread_count=2,
    migration_count=2,
    exchange_interval=1,
    topology="ring",
    timeout=20.0,
)


def early_finisher(config, inboxes, report_queue, start_queue):
    if config["process_id"] == 0:
        with patch("search.mutate_sequence", return_value=INITIAL_SEQUENCE):
            _process_entry(config, inboxes, report_queue, start_queue)
    else:
        _process_entry(config, inboxes, report_queue, start_queue)


def failing_evaluator(config, inboxes, report_queue, start_queue):
    if config["process_id"] == 0:
        with patch("search.oceni", side_effect=RuntimeError("Synthetic process failure")):
            _process_entry(config, inboxes, report_queue, start_queue)
    else:
        _process_entry(config, inboxes, report_queue, start_queue)


def abruptly_exiting(config, inboxes, report_queue, start_queue):
    if config["process_id"] == 0:
        os._exit(17)
    _process_entry(config, inboxes, report_queue, start_queue)


def unresponsive_worker(config, inboxes, report_queue, start_queue):
    if config["process_id"] == 0:
        report_queue.put({"kind": "ready", "process_id": 0})
        start_queue.get()
        sleep(60)
    else:
        _process_entry(config, inboxes, report_queue, start_queue)


class ProcessTests(unittest.TestCase):
    def assert_successful_run(self, result, budget, processes):
        self.assertFalse(result.failures)
        self.assertEqual(len(result.process_results), processes)
        self.assertEqual(
            sum(local.stats.evaluations_started for local in result.process_results), budget
        )
        self.assertEqual(
            sum(local.stats.evaluations_completed for local in result.process_results), budget
        )
        for local in result.process_results:
            self.assertEqual(local.budget, budget // processes)
            self.assertEqual(local.stats.evaluations_completed, local.budget)
            self.assertEqual(
                local.records_count,
                local.stats.evaluations_completed + local.stats.candidates_accepted,
            )
            self.assertLessEqual(local.stats.candidates_accepted, local.stats.candidates_received)
            self.assertEqual(len(local.archive), len({c.sequence for c in local.archive}))
            self.assertTrue(all(c.scores is not None for c in local.archive))
        all_candidates = [c for local in result.process_results for c in local.archive]
        unique = {candidate.sequence: candidate for candidate in all_candidates}
        expected = non_dominated_sort(list(unique.values()))[0]
        self.assertEqual(
            {c.sequence for c in result.archive}, {c.sequence for c in expected}
        )

    def test_neighbors(self):
        self.assertEqual(get_neighbors(0, 1, "ring"), [])
        self.assertEqual(get_neighbors(0, 2, "ring"), [1])
        self.assertEqual(get_neighbors(1, 2, "ring"), [0])
        self.assertEqual(get_neighbors(3, 4, "ring"), [0])
        self.assertEqual(get_neighbors(0, 4, "star"), [1, 2, 3])
        self.assertEqual(get_neighbors(2, 4, "star"), [0])
        self.assertEqual(get_neighbors(1, 4, "all-to-all"), [0, 2, 3])
        for topology in ("ring", "star", "all-to-all"):
            for count in (1, 2, 4):
                for process_id in range(count):
                    neighbors = get_neighbors(process_id, count, topology)
                    self.assertNotIn(process_id, neighbors)
                    self.assertEqual(len(neighbors), len(set(neighbors)))
        with self.assertRaises(ValueError):
            get_neighbors(0, 4, "unknown")

    def test_migrant_selection_and_exchange_interval(self):
        archive = [Candidate(str(i), (i, 10 - i, 5), origin_process=0) for i in range(5)]
        selected = select_migrants(archive, 2)
        self.assertEqual({c.sequence for c in selected}, {"0", "4"})
        self.assertEqual(len(select_migrants(archive, 20)), 5)
        inboxes = [Queue() for _ in range(3)]
        stats = SearchStats()
        send_candidates(archive, 1, stats, 0, 3, "all-to-all", 2, 2, inboxes)
        self.assertTrue(all(inbox.empty() for inbox in inboxes))
        send_candidates(archive, 2, stats, 0, 3, "all-to-all", 2, 2, inboxes)
        self.assertEqual(stats.messages_sent, 2)
        self.assertEqual(stats.candidates_sent, 4)
        self.assertTrue(inboxes[0].empty())
        for inbox in inboxes[1:]:
            message = inbox.get_nowait()
            self.assertEqual(message["iteration"], 2)
            self.assertEqual(len(message["candidates"]), 2)
            self.assertTrue(all(origin == 0 for _, _, origin in message["candidates"]))

    def test_receive_known_dominated_and_nondominated(self):
        known = Candidate("AAAA", (5, 5, 5), origin_process=0)
        records = {known.sequence: known}
        stats = SearchStats(1, 1)
        inbox = Queue()
        inbox.put({"candidates": [
            ("AAAA", (5, 5, 5), 1),
            ("CCCC", (1, 1, 1), 1),
            ("DDDD", (9, 1, 1), 2),
        ]})
        received = receive_candidates(inbox, records, stats)
        self.assertEqual([c.sequence for c in received], ["CCCC", "DDDD"])
        self.assertEqual(stats.candidates_received, 3)
        self.assertEqual(stats.candidates_accepted, 2)
        self.assertEqual(stats.evaluations_started, 1)
        self.assertEqual(stats.evaluations_completed, 1)
        self.assertIs(records["AAAA"], known)
        self.assertEqual(records["DDDD"].origin_process, 2)
        _, archive = update_population_and_archive([known], [known], received, 4)
        self.assertEqual({c.sequence for c in archive}, {"AAAA", "DDDD"})
        self.assertIn("CCCC", records)
        self.assertEqual(receive_candidates(inbox, records, stats), [])

    def test_imports_do_not_count_toward_M_or_change_parents_mid_iteration(self):
        imported = "C" * len(INITIAL_SEQUENCE)
        local_sequences = [letter * len(INITIAL_SEQUENCE) for letter in "VWY"]
        inbox = Queue()
        inbox.put({"candidates": [(imported, (10, 10, 10), 1)]})
        generated = iter(local_sequences)

        def generate(population, mutation_lambda, rng):
            self.assertNotIn(imported, [c.sequence for c in population])
            return next(generated)

        with patch("search.generate_offspring", side_effect=generate):
            result = run_threaded(
                INITIAL_SEQUENCE, 4, 2, 3, 2.0, 7,
                np.random.default_rng(42), 2,
                receive_hook=lambda records, stats: receive_candidates(inbox, records, stats),
            )
        self.assertEqual(result.stats.evaluations_started, 7)
        self.assertEqual(result.stats.evaluations_completed, 7)
        self.assertEqual(len(result.records), 8)
        self.assertEqual(result.stats.candidates_accepted, 1)
        self.assertEqual([c.sequence for c in result.archive], [imported])

    def test_receipt_does_not_prevent_duplicate_termination(self):
        inbox = Queue()
        inbox.put({"candidates": [("W" * len(INITIAL_SEQUENCE), (10, 10, 10), 1)]})
        with patch("search.generate_offspring", return_value=INITIAL_SEQUENCE) as generate:
            result = run_threaded(
                INITIAL_SEQUENCE, 4, 2, 3, 2.0, 20,
                np.random.default_rng(42), 4,
                receive_hook=lambda records, stats: receive_candidates(inbox, records, stats),
            )
        self.assertEqual(generate.call_count, 100)
        self.assertEqual(result.stop_reason, "duplicate_limit")
        self.assertEqual(result.stats.evaluations_started, 4)
        self.assertEqual(len(result.records), 5)
        self.assertEqual(len(result.iteration_seconds), 1)

    def test_merge_removes_duplicates_and_dominated_candidates(self):
        first = [Candidate("A", (5, 5, 5), origin_process=2), Candidate("B", (1, 1, 1))]
        second = [Candidate("A", (5, 5, 5), origin_process=1), Candidate("C", (9, 1, 1))]
        merged = merge_archives([first, second])
        self.assertEqual({c.sequence for c in merged}, {"A", "C"})
        self.assertEqual(next(c.origin_process for c in merged if c.sequence == "A"), 1)
        expected = non_dominated_sort(first + second)[0]
        self.assertEqual({c.sequence for c in merged}, {c.sequence for c in expected})
        self.assertEqual(merge_archives([]), [])

    def test_independent_repeatable_seed_streams(self):
        first = [tuple(np.random.default_rng(child).integers(0, 100000, size=5))
                 for child in np.random.SeedSequence(42).spawn(4)]
        second = [tuple(np.random.default_rng(child).integers(0, 100000, size=5))
                  for child in np.random.SeedSequence(42).spawn(4)]
        self.assertEqual(first, second)
        self.assertEqual(len(set(first)), 4)

    def test_budget_partition_and_processes_with_threads(self):
        result = run_multiprocess(**PARAMETERS, process_lambdas=[1.0, 3.0])
        self.assert_successful_run(result, 84, 2)
        self.assertEqual([local.mutation_lambda for local in result.process_results], [1.0, 3.0])
        for local in result.process_results:
            # (42 - 4) = 38 new local candidates: 12 full batches plus 2.
            self.assertEqual(len(local.iteration_seconds), 13)
            self.assertEqual(local.stats.messages_sent, 13)

    def test_all_topologies_real_processes(self):
        for topology in ("ring", "star", "all-to-all"):
            with self.subTest(topology=topology):
                result = run_multiprocess(**{
                    **PARAMETERS, "process_count": 4, "budget": 160,
                    "thread_count": 1, "topology": topology, "exchange_interval": 2,
                })
                self.assert_successful_run(result, 160, 4)
                for local in result.process_results:
                    neighbors = get_neighbors(local.process_id, 4, topology)
                    self.assertEqual(
                        local.stats.messages_sent,
                        (len(local.iteration_seconds) // 2) * len(neighbors),
                    )

    def test_finished_receiver_does_not_block_remaining_processes(self):
        result = run_multiprocess(**{
            **PARAMETERS, "process_count": 3, "budget": 1200,
            "topology": "all-to-all", "batch_size": 2,
        }, _worker_target=early_finisher)
        self.assertFalse(result.failures)
        first, *others = result.process_results
        self.assertEqual(first.process_id, 0)
        self.assertEqual(first.stop_reason, "duplicate_limit")
        self.assertEqual(first.stats.evaluations_completed, 1)
        self.assertTrue(all(local.stats.evaluations_completed == 400 for local in others))
        self.assertTrue(all(local.stats.candidates_sent > 0 for local in others))

    def test_caught_process_error_does_not_block_results(self):
        result = run_multiprocess(**PARAMETERS, _worker_target=failing_evaluator)
        self.assertIn(0, result.failures)
        self.assertIn("Synthetic process failure", result.failures[0])
        self.assertEqual([local.process_id for local in result.process_results], [1])
        self.assertEqual(result.process_results[0].stats.evaluations_completed, 42)

    def test_abrupt_process_exit_without_report(self):
        result = run_multiprocess(**PARAMETERS, _worker_target=abruptly_exiting)
        self.assertIn(0, result.failures)
        self.assertIn("17", result.failures[0])
        self.assertEqual([local.process_id for local in result.process_results], [1])

    def test_timeout_is_bounded(self):
        start = perf_counter()
        result = run_multiprocess(
            **{**PARAMETERS, "timeout": 2.0}, _worker_target=unresponsive_worker
        )
        self.assertIn(0, result.failures)
        self.assertIn("timeout", result.failures[0])
        self.assertLess(perf_counter() - start, 10)

    def test_invalid_parameters_fail_before_spawning(self):
        for changes in (
            {"budget": 85}, {"budget": 1}, {"process_count": 0},
            {"thread_count": 0}, {"exchange_interval": 0},
            {"migration_count": -1}, {"process_lambdas": [1]},
            {"process_lambdas": [1, -1]}, {"timeout": 0},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_multiprocess(**{**PARAMETERS, **changes})


if __name__ == "__main__":
    mp.freeze_support()
    unittest.main(verbosity=2)
