from __future__ import annotations

from collections.abc import Mapping

from .config import PersonConfig, ProcessingConfig
from .models import Assignment


class IdentityMatcher:
    def __init__(self, people: tuple[PersonConfig, ...], config: ProcessingConfig):
        self.people = people
        self.config = config

    def assign(
        self,
        weight_kg: float,
        recent_baselines: Mapping[str, float | None],
    ) -> Assignment:
        candidates = [
            person
            for person in self.people
            if person.min_weight_kg <= weight_kg <= person.max_weight_kg
        ]
        if not candidates:
            return Assignment(None, None, 0.0, "outside_all_person_ranges")
        if len(candidates) == 1:
            person = candidates[0]
            return Assignment(person.id, person.name, 1.0, "unique_weight_range")

        ranked: list[tuple[float, PersonConfig]] = []
        for person in candidates:
            baseline = recent_baselines.get(person.id)
            if baseline is None:
                baseline = person.initial_weight_kg
            ranked.append((abs(weight_kg - baseline), person))
        ranked.sort(key=lambda item: item[0])
        best_distance, best_person = ranked[0]
        second_distance = ranked[1][0]
        margin = second_distance - best_distance
        if margin < self.config.ambiguity_margin_kg:
            return Assignment(None, None, 0.0, "ambiguous_overlapping_ranges")
        confidence = min(0.99, 0.75 + margin / 100.0)
        return Assignment(best_person.id, best_person.name, confidence, "nearest_recent_baseline")
