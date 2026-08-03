import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from bot.cogs.scrim import ncl_leaderboard_rows


class NclLeaderboardTest(unittest.TestCase):
    def test_leaderboard_counts_wins_losses_and_maps_diff(self) -> None:
        team_a = SimpleNamespace(id=1, team_name="A", elo=530)
        team_b = SimpleNamespace(id=2, team_name="B", elo=500)
        match = SimpleNamespace(
            played_at=datetime.now(UTC),
            winner_team_id=1,
            team1_id=1,
            team2_id=2,
            team1_game_wins=3,
            team2_game_wins=1,
        )

        rows = ncl_leaderboard_rows([team_a, team_b], [match])

        self.assertEqual(team_a, rows[0]["team"])
        self.assertEqual(1, rows[0]["wins"])
        self.assertEqual(0, rows[0]["losses"])
        self.assertEqual(2, rows[0]["maps_won"] - rows[0]["maps_lost"])
        self.assertEqual(0, rows[1]["wins"])
        self.assertEqual(1, rows[1]["losses"])
        self.assertEqual(-2, rows[1]["maps_won"] - rows[1]["maps_lost"])


if __name__ == "__main__":
    unittest.main()
