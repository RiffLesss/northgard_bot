import unittest

from bot.services.draft_service import (
    ALL_CLANS,
    ClanRules,
    random_valid_picks,
    validate_team_pair,
    valid_single_pick_options,
)


class DraftRulesTest(unittest.TestCase):
    def test_default_pool_contains_raven(self) -> None:
        self.assertIn("Raven", ALL_CLANS)
        self.assertEqual(21, len(ALL_CLANS))

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

    def test_rules_can_use_database_loaded_clan_categories(self) -> None:
        rules = ClanRules(
            all_clans=["CustomClear", "CustomKingdomA", "CustomKingdomB", "Eco"],
            clear_clans={"CustomClear"},
            kingdom_clans={"CustomKingdomA", "CustomKingdomB"},
        )

        self.assertIsNotNone(validate_team_pair(["Snake", "CustomClear"], rules))
        self.assertIsNotNone(validate_team_pair(["CustomKingdomA", "CustomKingdomB"], rules))
        self.assertIsNone(validate_team_pair(["Snake", "Eco"], rules))


if __name__ == "__main__":
    unittest.main()
