import unittest

from bot.services.team_service import PLAYERS, PLAYERS_PER_ROUND, format_schedule, generate_schedule


class ScheduleTest(unittest.TestCase):
    def test_generate_schedule_shape(self) -> None:
        schedule = generate_schedule()

        self.assertEqual(5, len(schedule))
        for round_data in schedule:
            self.assertEqual(2, len(round_data["matches"]))
            active_count = sum(len(team) for match in round_data["matches"] for team in match)
            self.assertEqual(PLAYERS_PER_ROUND, active_count)
            self.assertEqual(len(PLAYERS), active_count + len(round_data["chilling"]))

    def test_format_schedule_can_hide_output_with_spoilers(self) -> None:
        schedule = [
            {
                "round": 1,
                "matches": [(["a", "b", "c"], ["d", "e", "f"])],
                "chilling": ["g"],
            }
        ]

        self.assertIn("||1\\. a b c VS d e f||", format_schedule(schedule, use_spoilers=True))


if __name__ == "__main__":
    unittest.main()
