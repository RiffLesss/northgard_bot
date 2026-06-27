import unittest

from bot.services.team3_service import (
    NORMAL_RATING_SPREAD,
    QueueEntry,
    TEAM3_DRAFT_STEPS,
    find_best_ranked_match,
    rating_delta,
    split_casual_players,
)


class DummyUser:
    def __init__(self, user_id: int):
        self.id = user_id


class Team3ServiceTest(unittest.TestCase):
    def entry(self, idx: int, rating: int, wide: bool = False) -> QueueEntry:
        return QueueEntry(user_id=idx, discord_id=idx, nickname=f"p{idx}", rating=rating, wide=wide)

    def test_normal_match_requires_rating_spread(self) -> None:
        entries = [self.entry(i, rating) for i, rating in enumerate([500, 520, 540, 560, 580, 500 + NORMAL_RATING_SPREAD])]

        self.assertIsNotNone(find_best_ranked_match(entries))

        entries[-1] = self.entry(99, 500 + NORMAL_RATING_SPREAD + 1)
        self.assertIsNone(find_best_ranked_match(entries))

    def test_wide_match_allows_large_spread_only_if_all_wide(self) -> None:
        ratings = [500, 600, 700, 900, 1100, 1300]

        self.assertIsNone(find_best_ranked_match([self.entry(i, rating) for i, rating in enumerate(ratings)]))
        self.assertIsNotNone(
            find_best_ranked_match([self.entry(i, rating, wide=True) for i, rating in enumerate(ratings)])
        )

    def test_casual_split_randomizes_six_solo_players(self) -> None:
        users = [DummyUser(index) for index in range(1, 7)]

        team_a, team_b = split_casual_players(users)  # type: ignore[arg-type]

        self.assertEqual(3, len(team_a))
        self.assertEqual(3, len(team_b))
        self.assertEqual({1, 2, 3, 4, 5, 6}, {user.id for user in [*team_a, *team_b]})

    def test_ranked_split_avoids_blacklist_conflicts_inside_team(self) -> None:
        entries = [self.entry(index, 500) for index in range(1, 7)]

        split = find_best_ranked_match(entries, {(1, 2)})

        self.assertIsNotNone(split)
        assert split is not None
        teams = [{entry.user_id for entry in split.team_a}, {entry.user_id for entry in split.team_b}]
        self.assertFalse(any({1, 2}.issubset(team) for team in teams))

    def test_casual_split_avoids_blacklist_conflicts_inside_team(self) -> None:
        users = [DummyUser(index) for index in range(1, 7)]

        team_a, team_b = split_casual_players(users, {(1, 2)}, attempts=1)  # type: ignore[arg-type]

        teams = [{user.id for user in team_a}, {user.id for user in team_b}]
        self.assertFalse(any({1, 2}.issubset(team) for team in teams))

    def test_casual_split_fails_when_blacklist_safe_split_is_impossible(self) -> None:
        users = [DummyUser(index) for index in range(1, 7)]
        blacklist_pairs = {(left, right) for left in range(1, 7) for right in range(1, 7) if left != right}

        with self.assertRaises(ValueError):
            split_casual_players(users, blacklist_pairs, attempts=1)  # type: ignore[arg-type]

    def test_team3_draft_has_expected_steps(self) -> None:
        self.assertEqual(14, len(TEAM3_DRAFT_STEPS))
        first_step = TEAM3_DRAFT_STEPS[0]
        self.assertEqual("A", first_step.side)
        self.assertEqual("ban", first_step.action_type.value)
        self.assertEqual("clear", first_step.pick_type.value)

    def test_rating_delta_is_higher_when_favorite_loses(self) -> None:
        upset_loss = rating_delta(900, 1300)
        expected_loss = rating_delta(1300, 900)

        self.assertGreater(upset_loss, expected_loss)


if __name__ == "__main__":
    unittest.main()
