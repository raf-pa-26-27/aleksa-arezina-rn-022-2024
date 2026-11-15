import math
import multiprocessing as mp
from queue import Empty, Queue
from threading import Event, Thread
from time import perf_counter
import traceback

import numpy as np

from model import Candidate, MultiprocessResult, ProcessResult, SearchStats
from pareto import calculate_crowding_distance, non_dominated_sort
from search import run_threaded, validate_parameters


TOPOLOGIES = ("ring", "star", "all-to-all")


def get_neighbors(process_id: int, process_count: int, topology: str) -> list[int]:
    if topology not in TOPOLOGIES:
        raise ValueError(f"topology must be one of {TOPOLOGIES}")
    if process_count < 1 or not 0 <= process_id < process_count:
        raise ValueError("invalid process count or process id")
    if process_count == 1:
        return []
    if topology == "ring":
        return [(process_id + 1) % process_count]
    if topology == "star":
        return list(range(1, process_count)) if process_id == 0 else [0]
    return [index for index in range(process_count) if index != process_id]


def select_migrants(archive: list[Candidate], migration_count: int) -> list[Candidate]:
    calculate_crowding_distance(archive)
    return sorted(
        archive, key=lambda candidate: (-candidate.crowding_distance, candidate.sequence)
    )[:migration_count]


def send_candidates(
    archive: list[Candidate],
    iteration_number: int,
    stats: SearchStats,
    process_id: int,
    process_count: int,
    topology: str,
    migration_count: int,
    exchange_interval: int,
    inboxes,
) -> None:
    if iteration_number % exchange_interval != 0 or migration_count == 0:
        return
    migrants = select_migrants(archive, migration_count)
    if not migrants:
        return

    message = {
        "sender": process_id,
        "iteration": iteration_number,
        "candidates": [
            (candidate.sequence, candidate.scores, candidate.origin_process)
            for candidate in migrants
        ],
    }
    for neighbor in get_neighbors(process_id, process_count, topology):
        inboxes[neighbor].put_nowait(message)
        stats.messages_sent += 1
        stats.candidates_sent += len(migrants)


def receive_candidates(inbox, records: dict[str, Candidate], stats: SearchStats):
    received = []
    while True:
        try:
            message = inbox.get_nowait()
        except Empty:
            break
        stats.messages_received += 1
        for sequence, scores, origin in message["candidates"]:
            stats.candidates_received += 1
            if sequence in records:
                continue
            candidate = Candidate(sequence, tuple(scores), origin_process=origin)
            records[sequence] = candidate
            received.append(candidate)
            stats.candidates_accepted += 1
    return received


def merge_archives(archives: list[list[Candidate]]) -> list[Candidate]:
    unique = {}
    for archive in archives:
        for candidate in archive:
            previous = unique.get(candidate.sequence)
            if previous is not None and previous.scores != candidate.scores:
                raise ValueError("identical sequences have inconsistent scores")
            if previous is None or candidate.origin_process < previous.origin_process:
                unique[candidate.sequence] = Candidate(
                    candidate.sequence,
                    candidate.scores,
                    origin_process=candidate.origin_process,
                )
    fronts = non_dominated_sort([unique[key] for key in sorted(unique)])
    archive = fronts[0] if fronts else []
    calculate_crowding_distance(archive)
    return archive


def _process_entry(config, inboxes, report_queue, start_queue):
    process_id = config["process_id"]
    try:
        report_queue.put({"kind": "ready", "process_id": process_id})
        start_queue.get()
        local_start = perf_counter()
        rng = np.random.default_rng(config["seed_sequence"])

        def receive(records, stats):
            return receive_candidates(inboxes[process_id], records, stats)

        def send(archive, iteration_number, stats):
            send_candidates(
                archive, iteration_number, stats,
                process_id, config["process_count"], config["topology"],
                config["migration_count"], config["exchange_interval"], inboxes,
            )

        result = run_threaded(
            **config["search_parameters"],
            rng=rng,
            thread_count=config["thread_count"],
            process_id=process_id,
            receive_hook=receive,
            send_hook=send,
        )
        local_result = ProcessResult(
            process_id=process_id,
            mutation_lambda=config["search_parameters"]["mutation_lambda"],
            budget=config["search_parameters"]["budget"],
            archive=result.archive,
            stats=result.stats,
            stop_reason=result.stop_reason,
            iteration_seconds=result.iteration_seconds,
            total_seconds=perf_counter() - local_start,
            records_count=len(result.records),
        )
        report_queue.put({
            "kind": "result", "process_id": process_id, "result": local_result
        })
    except BaseException:
        report_queue.put({
            "kind": "error", "process_id": process_id,
            "error": traceback.format_exc(),
        })
    finally:
        report_queue.close()
        report_queue.join_thread()
        for inbox in inboxes:
            inbox.close()
            inbox.join_thread()


def _read_reports(process_id, channel, events, stop):
    try:
        while not stop.is_set():
            try:
                message = channel.get(timeout=0.1)
            except Empty:
                continue
            events.put(message)
            if message["kind"] in ("result", "error"):
                return
    except (EOFError, OSError, ValueError) as error:
        if not stop.is_set():
            events.put({"kind": "error", "process_id": process_id, "error": str(error)})


def _discard_messages(inbox, stop):
    try:
        while not stop.is_set():
            try:
                inbox.get(timeout=0.1)
            except Empty:
                continue
    except (EOFError, OSError, ValueError):
        return


def run_multiprocess(
    initial_sequence: str,
    population_size: int,
    max_initial_mutations: int,
    batch_size: int,
    mutation_lambda: float,
    budget: int,
    seed: int,
    process_count: int,
    thread_count: int,
    migration_count: int,
    exchange_interval: int,
    topology: str,
    process_lambdas: list[float] | None = None,
    timeout: float = 300.0,
    *,
    _worker_target=None,
) -> MultiprocessResult:
    initial_sequence = initial_sequence.upper().strip()
    for name, value, minimum in (
        ("process_count", process_count, 1),
        ("thread_count", thread_count, 1),
        ("migration_count", migration_count, 0),
        ("exchange_interval", exchange_interval, 1),
        ("seed", seed, 0),
    ):
        if type(value) is not int or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if type(budget) is not int or budget < process_count or budget % process_count:
        raise ValueError("budget must be positive, divisible by processes, and >= processes")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    get_neighbors(0, process_count, topology)
    lambdas = list(process_lambdas) if process_lambdas is not None else [mutation_lambda] * process_count
    if len(lambdas) != process_count:
        raise ValueError("provide exactly one mutation lambda per process")
    local_budget = budget // process_count
    for value in lambdas:
        validate_parameters(
            initial_sequence, population_size, max_initial_mutations,
            batch_size, value, local_budget,
        )

    context = mp.get_context("spawn")
    inboxes = [context.Queue() for _ in range(process_count)]
    reports = [context.Queue() for _ in range(process_count)]
    start_queue = context.Queue()
    seed_sequences = np.random.SeedSequence(seed).spawn(process_count)
    events = Queue()
    stop_readers = Event()
    threads = []
    drainers = {}
    children = {}
    failures = {}
    results = {}
    ready = set()
    terminal = set()
    dead_since = {}
    started_at = {}
    released = False
    deadline = perf_counter() + timeout

    def drain_finished_inbox(process_id):
        if process_id not in drainers:
            thread = Thread(
                target=_discard_messages,
                args=(inboxes[process_id], stop_readers), daemon=True,
                name=f"discard-{process_id}",
            )
            drainers[process_id] = thread
            thread.start()

    try:
        for process_id in range(process_count):
            config = {
                "process_id": process_id,
                "process_count": process_count,
                "thread_count": thread_count,
                "seed_sequence": seed_sequences[process_id],
                "topology": topology,
                "migration_count": migration_count,
                "exchange_interval": exchange_interval,
                "search_parameters": {
                    "initial_sequence": initial_sequence,
                    "population_size": population_size,
                    "max_initial_mutations": max_initial_mutations,
                    "batch_size": batch_size,
                    "mutation_lambda": lambdas[process_id],
                    "budget": local_budget,
                },
            }
            child = context.Process(
                target=_worker_target or _process_entry,
                args=(config, inboxes, reports[process_id], start_queue),
                name=f"island-{process_id}",
            )
            try:
                started_at[process_id] = perf_counter()
                child.start()
            except Exception as error:
                failures[process_id] = f"Could not start process: {error}"
                terminal.add(process_id)
                drain_finished_inbox(process_id)
                continue
            children[process_id] = child
            reader = Thread(
                target=_read_reports,
                args=(process_id, reports[process_id], events, stop_readers),
                daemon=True, name=f"report-{process_id}",
            )
            threads.append(reader)
            reader.start()

        while True:
            try:
                message = events.get(timeout=0.02)
            except Empty:
                message = None
            if message is not None:
                process_id = message["process_id"]
                if process_id not in terminal:
                    if message["kind"] == "ready":
                        ready.add(process_id)
                    else:
                        terminal.add(process_id)
                        if message["kind"] == "result":
                            results[process_id] = message["result"]
                        else:
                            failures[process_id] = message["error"]
                        drain_finished_inbox(process_id)

            for process_id, child in children.items():
                if child.exitcode is not None:
                    dead_since.setdefault(process_id, perf_counter())
                    if child.exitcode != 0:
                        failures.setdefault(process_id, f"Process exited with code {child.exitcode}")
                        terminal.add(process_id)
                        drain_finished_inbox(process_id)
                    elif process_id not in terminal and perf_counter() - dead_since[process_id] > 1.0:
                        failures[process_id] = "Process exited without a final report"
                        terminal.add(process_id)
                        drain_finished_inbox(process_id)

            if not released and len(ready | terminal) == process_count:
                for process_id in range(process_count):
                    if process_id not in terminal:
                        start_queue.put("start")
                released = True

            if len(terminal) == process_count and all(
                child.exitcode is not None for child in children.values()
            ):
                break
            if perf_counter() >= deadline:
                for process_id, child in children.items():
                    if child.is_alive():
                        failures[process_id] = f"Coordinator timeout after {timeout} seconds"
                        child.terminate()
                break
    finally:
        for child in children.values():
            if child.is_alive():
                child.terminate()
            child.join(timeout=1.0)
            if child.is_alive():
                child.kill()
                child.join(timeout=1.0)
        stop_readers.set()
        for thread in threads + list(drainers.values()):
            thread.join(timeout=0.2)
        for channel in inboxes + reports + [start_queue]:
            channel.cancel_join_thread()
            channel.close()

    ordered_results = [results[index] for index in sorted(results)]
    for result in ordered_results:
        result.lifecycle_seconds = (
            dead_since.get(result.process_id, perf_counter()) - started_at[result.process_id]
        )
    archive = merge_archives([result.archive for result in ordered_results])
    return MultiprocessResult(archive, ordered_results, failures)
