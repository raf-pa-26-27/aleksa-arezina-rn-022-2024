from dataclasses import dataclass, field


@dataclass
class Candidate:
    sequence: str
    scores: tuple[float, float, float] | None = None
    rank: int = 0
    crowding_distance: float = 0.0

@dataclass
class SearchStats:
    evaluations_started: int = 0
    evaluations_completed: int = 0

@dataclass
class SearchResult:
    archive: list[Candidate]
    records: dict[str, Candidate]
    stop_reason: str
    iteration_seconds: list[float]
    stats: SearchStats = field(default_factory=SearchStats)

