from dataclasses import dataclass, field


@dataclass
class Candidate:
    sequence: str
    scores: tuple[float, float, float] | None = None
    rank: int = 0
    crowding_distance: float = 0.0
    origin_process: int = 0

@dataclass
class SearchStats:
    evaluations_started: int = 0
    evaluations_completed: int = 0
    candidates_sent: int = 0
    candidates_received: int = 0
    candidates_accepted: int = 0
    messages_sent: int = 0
    messages_received: int = 0

@dataclass
class SearchResult:
    archive: list[Candidate]
    records: dict[str, Candidate]
    stop_reason: str
    iteration_seconds: list[float]
    stats: SearchStats = field(default_factory=SearchStats)


@dataclass
class ProcessResult:
    process_id: int
    mutation_lambda: float
    budget: int
    archive: list[Candidate]
    stats: SearchStats
    stop_reason: str
    iteration_seconds: list[float]
    total_seconds: float
    records_count: int
    lifecycle_seconds: float | None = None


@dataclass
class MultiprocessResult:
    archive: list[Candidate]
    process_results: list[ProcessResult]
    failures: dict[int, str]

