from concurrent.futures import ThreadPoolExecutor

from model import Candidate


def non_dominated_sort(
    candidates: list[Candidate],
) -> list[list[Candidate]]:
    remaining = candidates.copy()
    fronts = []
    rank = 1

    while remaining:
        current_front = []
        for candidate in remaining:
            is_dominated = False

            for other in remaining:
                if other is candidate:
                    continue

                if dominates(other.scores, candidate.scores):
                    is_dominated = True
                    break

            if not is_dominated:
                current_front.append(candidate)

        for candidate in current_front:
            candidate.rank = rank
            remaining.remove(candidate)
        fronts.append(current_front)
        rank += 1
        

    return fronts
    
def dominates(a, b):
    is_better = False

    for x, y in zip(a, b):
        if x < y:
            return False

        if x > y:
            is_better = True

    return is_better


def non_dominated_sort_parallel(
    candidates: list[Candidate],
    pool: ThreadPoolExecutor,
) -> list[list[Candidate]]:
    remaining = candidates.copy()
    fronts = []
    rank = 1

    while remaining:
        snapshot = tuple(remaining)

        def is_non_dominated(candidate):
            for other in snapshot:
                if other is candidate:
                    continue
                if dominates(other.scores, candidate.scores):
                    return False
            return True

        flags = list(pool.map(is_non_dominated, snapshot))
        front = [candidate for candidate, keep in zip(snapshot, flags) if keep]
        for candidate in front:
            candidate.rank = rank
            remaining.remove(candidate)
        fronts.append(front)
        rank += 1

    return fronts

def calculate_crowding_distance(front: list[Candidate]) -> None:
    for candidate in front:
        candidate.crowding_distance = 0.0

    if not front:
        return

    for objective in range(3):
        ordered = sorted(
            front,
            key=lambda candidate: candidate.scores[objective],
        )
        min_score = ordered[0].scores[objective]
        max_score = ordered[-1].scores[objective]
        score_range = max_score - min_score
        if score_range == 0:
            continue
        ordered[0].crowding_distance = float("inf")
        ordered[-1].crowding_distance = float("inf")

        for i in range(1, len(ordered) - 1):
            ordered[i].crowding_distance += (ordered[i+1].scores[objective] - ordered[i-1].scores[objective]) / score_range

def update_population_and_archive(
    population: list[Candidate],
    archive: list[Candidate],
    new_candidates: list[Candidate],
    population_size: int,
    ranking_pool: ThreadPoolExecutor | None = None,
) -> tuple[list[Candidate], list[Candidate]]:
    
    unique_candidates: dict[str, Candidate] = {}

    for candidate in population + archive + new_candidates:
        unique_candidates[candidate.sequence] = candidate
    combined = list(unique_candidates.values())

    if ranking_pool is None:
        fronts = non_dominated_sort(combined)
    else:
        fronts = non_dominated_sort_parallel(combined, ranking_pool)
    for front in fronts:
        calculate_crowding_distance(front)
    new_archive = fronts[0] if fronts else []
    new_population = []

    for front in fronts:
        free_slots = population_size - len(new_population)
        if free_slots == 0:
            break
        if free_slots >= len(front):
            new_population.extend(front)
        else:
            ordered = sorted(
                front,
                key=lambda candidate: candidate.crowding_distance,
                reverse=True,
            )
            new_population.extend(ordered[:free_slots])
            break

    return new_population, new_archive

