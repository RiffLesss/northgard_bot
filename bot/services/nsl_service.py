from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ScheduledPair:
    week_number: int
    week_start: date
    week_end: date
    team1_id: int
    team2_id: int


def nsl_expected_score(team_rating: float, opponent_rating: float) -> float:
    return 1 / (1 + 10 ** ((opponent_rating - team_rating) / 400))


def nsl_series_score(team_wins: int, opponent_wins: int) -> float:
    return (team_wins + 0.5) / (team_wins + opponent_wins + 1)


def nsl_new_rating(team_rating: int, opponent_rating: int, team_wins: int, opponent_wins: int) -> int:
    expected = nsl_expected_score(team_rating, opponent_rating)
    score = nsl_series_score(team_wins, opponent_wins)
    return round(team_rating + 64 * (score - expected))


def nsl_rating_update(
    team1_rating: int,
    team2_rating: int,
    team1_wins: int,
    team2_wins: int,
) -> tuple[int, int]:
    return (
        nsl_new_rating(team1_rating, team2_rating, team1_wins, team2_wins),
        nsl_new_rating(team2_rating, team1_rating, team2_wins, team1_wins),
    )


def next_monday(current_date: date) -> date:
    days_until_monday = (7 - current_date.weekday()) % 7
    return current_date + timedelta(days=days_until_monday)


def week_label(week_start: date) -> tuple[date, date]:
    return week_start, week_start + timedelta(days=6)


def round_robin_rounds(team_ids: list[int]) -> list[list[tuple[int, int]]]:
    if len(team_ids) < 4 or len(team_ids) % 2 != 0:
        raise ValueError("NSL schedule needs an even number of NSL teams, minimum 4.")

    teams = team_ids.copy()
    rounds = []
    for _ in range(len(teams) - 1):
        matches = []
        for index in range(len(teams) // 2):
            left = teams[index]
            right = teams[-index - 1]
            matches.append((min(left, right), max(left, right)))
        rounds.append(matches)
        teams = [teams[0], teams[-1], *teams[1:-1]]
    return rounds


def generate_nsl_schedule(team_ids: list[int], start_date: date) -> list[ScheduledPair]:
    rounds = round_robin_rounds(team_ids)
    schedule: list[ScheduledPair] = []
    for index, first_round in enumerate(rounds):
        second_round = rounds[(index + 1) % len(rounds)]
        week_start, week_end = week_label(start_date + timedelta(days=7 * index))
        seen_pairs: set[tuple[int, int]] = set()
        for team1_id, team2_id in [*first_round, *second_round]:
            pair = (min(team1_id, team2_id), max(team1_id, team2_id))
            if pair in seen_pairs:
                raise ValueError("Generated invalid schedule with duplicate weekly opponent.")
            seen_pairs.add(pair)
            schedule.append(
                ScheduledPair(
                    week_number=index + 1,
                    week_start=week_start,
                    week_end=week_end,
                    team1_id=pair[0],
                    team2_id=pair[1],
                )
            )
    return schedule
