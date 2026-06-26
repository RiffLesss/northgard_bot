import unittest

from bot.services.draft_service import (
    ALL_CLANS,
    random_valid_picks,
    validate_team_pair,
    valid_single_pick_options,
)


class DraftRulesTest(unittest.TestCase):
    def test_rejects_duplicate_clans(self) -> None:
        self.assertIsNotNone(validate_team_pair(["Wolf", "Wolf"]))

    def test_rejects_snake_with_clear_clan(self) -> None:
        self.assertIsNotNone(validate_team_pair(["Snake", "Wolf"]))

    def test_rejects_two_kingdom_clans(self) -> None:
        self.assertIsNotNone(validate_team_pair(["Lion", "Stoat"]))

    def test_valid_single_pick_options_exclude_invalid_pairings(self) -> None:
        options = valid_single_pick_options(ALL_CLANS, ["Snake"])

        self.assertNotIn("Wolf", options)
        self.assertIn("Bear", options)

    def test_random_valid_pick_respects_current_team(self) -> None:
        picks = random_valid_picks(1, ["Wolf", "Bear"], ["Snake"])

        self.assertEqual(["Bear"], picks)


if __name__ == "__main__":
    unittest.main()
