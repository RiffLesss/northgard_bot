import unittest
from collections import Counter, defaultdict
from datetime import date

from bot.services.ncl_service import (
    generate_ncl_schedule,
    ncl_expected_score,
    ncl_rating_update,
    ncl_series_score,
    next_monday,
)


class NclServiceTest(unittest.TestCase):
    def test_elo_matches_pdf_formula(self) -> None:
        self.assertAlmostEqual(0.5, ncl_expected_score(500, 500))
        self.assertEqual(0.875, ncl_series_score(3, 0))
        self.assertEqual((524, 476), ncl_rating_update(500, 500, 3, 0))

    def test_next_monday(self) -> None:
        self.assertEqual(date(2026, 8, 3), next_monday(date(2026, 8, 1)))
        self.assertEqual(date(2026, 8, 3), next_monday(date(2026, 8, 3)))

    def test_schedule_has_two_games_per_team_per_week(self) -> None:
        schedule = generate_ncl_schedule([1, 2, 3, 4, 5, 6], date(2026, 8, 3))

        self.assertEqual(5, max(match.week_number for match in schedule))
        for week in range(1, 6):
            week_matches = [match for match in schedule if match.week_number == week]
            weekly_counts = Counter()
            weekly_pairs = set()
            for match in week_matches:
                pair = (match.team1_id, match.team2_id)
                self.assertNotIn(pair, weekly_pairs)
                weekly_pairs.add(pair)
                weekly_counts[match.team1_id] += 1
                weekly_counts[match.team2_id] += 1
            self.assertTrue(all(count == 2 for count in weekly_counts.values()))

    def test_schedule_pairs_play_twice_total(self) -> None:
        schedule = generate_ncl_schedule([1, 2, 3, 4, 5, 6], date(2026, 8, 3))
        pair_counts = defaultdict(int)
        for match in schedule:
            pair_counts[(match.team1_id, match.team2_id)] += 1

        self.assertEqual(15, len(pair_counts))
        self.assertTrue(all(count == 2 for count in pair_counts.values()))

    def test_schedule_rejects_odd_or_too_small_team_count(self) -> None:
        with self.assertRaises(ValueError):
            generate_ncl_schedule([1, 2, 3], date(2026, 8, 3))
        with self.assertRaises(ValueError):
            generate_ncl_schedule([1, 2], date(2026, 8, 3))


if __name__ == "__main__":
    unittest.main()
