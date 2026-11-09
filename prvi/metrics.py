from model import Candidate


def calculate_score_ranges(
    archive: list[Candidate],
) -> list[tuple[float, float]]:
    if not archive:
        return []

    ranges = []

    for objective in range(3):
        values = [
            candidate.scores[objective]
            for candidate in archive
        ]

        ranges.append((min(values), max(values)))

    return ranges

def hv2d(
    points: list[tuple[float, float]],
    reference: tuple[float, float],
) -> float:
    area = 0.0
    best_y = reference[1]

    for x, y in sorted(points, reverse=True):
        if y > best_y:
            area += (x - reference[0]) * (y - best_y)
            best_y = y

    return area


def hv3d(
    points: list[tuple[float, float, float]],
    reference: tuple[float, float, float],
) -> float:
    ordered = sorted(points, key=lambda point: point[2], reverse=True)
    volume = 0.0

    for i, (_, _, z) in enumerate(ordered):
        next_z = (
            ordered[i + 1][2]
            if i + 1 < len(ordered)
            else reference[2]
        )

        height = z - next_z

        if height > 0:
            layer = [
                (x, y)
                for x, y, _ in ordered[:i + 1]
            ]

            volume += hv2d(layer, reference[:2]) * height

    return volume


def normalize(points, lower_bounds, upper_bounds):
    return [
        tuple(
            (value - lower) / (upper - lower)
            for value, lower, upper in zip(
                point, lower_bounds, upper_bounds
            )
        )
        for point in points
    ]


def calculate_hypervolume(archive: list[Candidate]) -> float:
    scores = [candidate.scores for candidate in archive]

    normalized_points = normalize(
        scores,
        lower_bounds=(0, 0, 0),
        upper_bounds=(10, 10, 10),
    )

    return hv3d(
        normalized_points,
        reference=(-0.1, -0.1, -0.1),
    )
