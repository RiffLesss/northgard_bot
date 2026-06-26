import random
from itertools import combinations


PLAYERS = [
    "asd",
    "riffless",
    "skarabei",
    "tetonka",
    "valkyrie",
    "vladiworld",
    "dumpling",
    "redz",
    "mori",
    "herewego",
    "bilge",
    "ssd",
    "bardus",
    "cat",
    "zombi",
]

MISMATCHES = {
    frozenset(("valkyrie", "bilge")),
    frozenset(("tetonka", "riffless")),
    frozenset(("bardus", "zombi")),
    frozenset(("bardus", "mori")),
}

FIXED_CHILLING = {
    0: "zombi",
    4: "dumpling",
}

ROUNDS_COUNT = 5
TEAM_SIZE = 3
TEAMS_PER_ROUND = 4
PLAYERS_PER_ROUND = TEAM_SIZE * TEAMS_PER_ROUND


def has_mismatch(team: list[str]) -> bool:
    for player_1, player_2 in combinations(team, 2):
        if frozenset((player_1, player_2)) in MISMATCHES:
            return True
    return False


def build_chilling_plan() -> list[list[str]]:
    chilling_plan = [[] for _ in range(ROUNDS_COUNT)]
    reserved_players = set()

    for round_index, player in FIXED_CHILLING.items():
        chilling_plan[round_index].append(player)
        reserved_players.add(player)

    remaining_players = [player for player in PLAYERS if player not in reserved_players]
    random.shuffle(remaining_players)

    fill_order = list(range(ROUNDS_COUNT))
    random.shuffle(fill_order)

    for round_index in fill_order:
        while len(chilling_plan[round_index]) < len(PLAYERS) - PLAYERS_PER_ROUND:
            chilling_plan[round_index].append(remaining_players.pop())

    for chilling in chilling_plan:
        chilling.sort()

    return chilling_plan


def build_teams(active_players: list[str]) -> list[list[str]] | None:
    players_pool = active_players.copy()
    random.shuffle(players_pool)

    def backtrack(remaining_players: list[str], current_teams: list[list[str]]) -> list[list[str]] | None:
        if not remaining_players:
            return [team.copy() for team in current_teams]

        captain = remaining_players[0]
        candidates = list(combinations(remaining_players[1:], TEAM_SIZE - 1))
        random.shuffle(candidates)

        for teammates in candidates:
            team = [captain, *teammates]
            if has_mismatch(team):
                continue

            next_remaining = [player for player in remaining_players if player not in team]
            current_teams.append(team)
            result = backtrack(next_remaining, current_teams)
            if result is not None:
                return result
            current_teams.pop()

        return None

    return backtrack(players_pool, [])


def generate_schedule(max_attempts: int = 10_000) -> list[dict[str, object]]:
    for _ in range(max_attempts):
        chilling_plan = build_chilling_plan()
        schedule = []

        for round_index, chilling in enumerate(chilling_plan, start=1):
            active_players = [player for player in PLAYERS if player not in chilling]
            teams = build_teams(active_players)
            if teams is None:
                break

            random.shuffle(teams)
            schedule.append(
                {
                    "round": round_index,
                    "matches": [
                        (teams[0], teams[1]),
                        (teams[2], teams[3]),
                    ],
                    "chilling": chilling,
                }
            )
        else:
            return schedule

    raise RuntimeError("Could not generate a valid schedule with the current constraints")


def format_schedule(schedule: list[dict[str, object]], use_spoilers: bool = False) -> str:
    lines: list[str] = []

    for round_index, round_data in enumerate(schedule):
        lines.append(f"# Round {round_data['round']}")

        matches = round_data["matches"]
        for match_index, match in enumerate(matches, start=1):
            team_1, team_2 = match
            line = f"{match_index}\\. {' '.join(team_1)} VS {' '.join(team_2)}"
            lines.append(f"||{line}||" if use_spoilers else line)

        lines.append("")
        chilling_line = f"Chilling: {' '.join(round_data['chilling'])}"
        lines.append(f"||{chilling_line}||" if use_spoilers else chilling_line)

        if round_index < len(schedule) - 1:
            lines.append("")

    return "\n".join(lines).strip()


def main() -> None:
    schedule = generate_schedule()
    print(format_schedule(schedule))


if __name__ == "__main__":
    main()
