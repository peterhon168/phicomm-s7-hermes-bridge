from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_scale.config import PersonConfig
from hermes_scale.identity import IdentityMatcher

from tests.helpers import make_config


class IdentityMatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self.temp.name))
        self.matcher = IdentityMatcher(self.config.people, self.config.processing)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_assigns_person_a(self) -> None:
        assignment = self.matcher.assign(80.0, {"person_a": None, "person_b": None})
        self.assertEqual(assignment.person_id, "person_a")
        self.assertEqual(assignment.reason, "unique_weight_range")

    def test_assigns_person_b(self) -> None:
        assignment = self.matcher.assign(55.0, {"person_a": None, "person_b": None})
        self.assertEqual(assignment.person_id, "person_b")

    def test_gap_goes_to_pending(self) -> None:
        assignment = self.matcher.assign(67.0, {"person_a": 80.0, "person_b": 55.0})
        self.assertIsNone(assignment.person_id)
        self.assertEqual(assignment.reason, "outside_all_person_ranges")

    def test_overlapping_ranges_need_a_clear_baseline_winner(self) -> None:
        people = (
            PersonConfig("a", "A", 50.0, 80.0, 60.0),
            PersonConfig("b", "B", 50.0, 80.0, 70.0),
        )
        config = make_config(Path(self.temp.name), people=people)
        matcher = IdentityMatcher(config.people, config.processing)
        pending = matcher.assign(65.0, {"a": 64.0, "b": 66.0})
        self.assertIsNone(pending.person_id)
        assigned = matcher.assign(60.0, {"a": 60.0, "b": 75.0})
        self.assertEqual(assigned.person_id, "a")


if __name__ == "__main__":
    unittest.main()
