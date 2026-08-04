import unittest
from types import SimpleNamespace

from bot.cogs.scrim import ScrimContext, ScrimDraftStep, ScrimDraftView
from bot.models.enums import DraftActionType, PickType


class NslDraftTest(unittest.TestCase):
    def test_opposing_team_can_pick_same_clan(self) -> None:
        team_a = SimpleNamespace(id=1, name="Team A", mention="@TeamA")
        team_b = SimpleNamespace(id=2, name="Team B", mention="@TeamB")
        context = ScrimContext(
            guild=None,  # type: ignore[arg-type]
            channel=None,  # type: ignore[arg-type]
            team_a_role=team_a,  # type: ignore[arg-type]
            team_b_role=team_b,  # type: ignore[arg-type]
            team_a_nsl_id=1,
            team_b_nsl_id=2,
            team_a_members=[],
            team_b_members=[],
            clear_clans=["Wolf"],
            eco_clans=["Goat", "Bear"],
        )
        view = ScrimDraftView(None, context)  # type: ignore[arg-type]
        view.picks["A"].append("Goat")

        team_b_pick_options = view.available_options(ScrimDraftStep("B", DraftActionType.PICK, PickType.ECO))
        team_a_pick_options = view.available_options(ScrimDraftStep("A", DraftActionType.PICK, PickType.ECO))
        ban_options = view.available_options(ScrimDraftStep("B", DraftActionType.BAN, PickType.ECO))

        self.assertIn("Goat", team_b_pick_options)
        self.assertNotIn("Goat", team_a_pick_options)
        self.assertNotIn("Goat", ban_options)

    def test_fearless_blocks_only_same_team_previous_eco_picks(self) -> None:
        team_a = SimpleNamespace(id=1, name="Team A", mention="@TeamA")
        team_b = SimpleNamespace(id=2, name="Team B", mention="@TeamB")
        context = ScrimContext(
            guild=None,  # type: ignore[arg-type]
            channel=None,  # type: ignore[arg-type]
            team_a_role=team_a,  # type: ignore[arg-type]
            team_b_role=team_b,  # type: ignore[arg-type]
            team_a_nsl_id=1,
            team_b_nsl_id=2,
            team_a_members=[],
            team_b_members=[],
            clear_clans=["Wolf"],
            eco_clans=["Goat", "Bear"],
            team_a_previous_eco_picks={"Goat"},
        )
        view = ScrimDraftView(None, context)  # type: ignore[arg-type]

        team_a_options = view.available_options(ScrimDraftStep("A", DraftActionType.PICK, PickType.ECO))
        team_b_options = view.available_options(ScrimDraftStep("B", DraftActionType.PICK, PickType.ECO))

        self.assertNotIn("Goat", team_a_options)
        self.assertIn("Goat", team_b_options)


if __name__ == "__main__":
    unittest.main()
