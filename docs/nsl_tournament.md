# NSL Tournament

## Teams

NSL teams are stored in PostgreSQL table `nsl_teams`.

Important fields:

- `team_name` - team name;
- `elo` - current team rating, default `500`;
- `discord_role_id` - Discord team role;
- `text_channel_id` - private team text channel;
- `voice_channel_id` - private team voice channel.

Team members are stored in `nsl_team_members`. Every player must already be registered with `/register` before they can be added to an NSL team.

## Schedule

Organizers generate the schedule with:

```text
/create_schedule
```

The bot uses all registered NSL teams, sorted by team name for stable generation.
Existing generated schedule can be recreated only before any NSL match has been played.

Schedule rules:

- even number of NSL teams is required;
- at least 4 NSL teams are required;
- every team plays 2 matches each week;
- every pair of teams plays exactly 2 matches across the schedule;
- the same pair cannot play twice in one week.

The first week starts on the next Monday. If the command is used on Monday, the first week starts on that same day.

The current schedule can be shown with:

```text
/nsl_schedule
```

Played matches are marked with `✅`.

## Leaderboard

The NSL team leaderboard is shown with:

```text
/nsl_leaderboard
```

This command can be used only in channel `1533831143034454127`.

The leaderboard message is reused and updated instead of creating duplicates. It is also refreshed automatically whenever a scheduled NSL match is completed.

Leaderboard columns:

- team name;
- Elo;
- wins count;
- loss count;
- maps diff, calculated as `MAPS WON - MAPS LOST`.

## Starting A Scheduled Match

A scheduled match is started with:

```text
/start_match @team1 @team2
```

The command works only when:

- both roles are registered NSL teams;
- there is an unplayed scheduled match between these teams in the current week;
- the command user is an organizer or a member of one of the two teams.

After the command:

1. The bot creates a private match text channel in category `1526212599820062982`.
2. Both teams receive a ready-check.
3. At least one player from each team must confirm within 2 minutes.
4. A bo5 match starts using the same draft flow as NSL scrims.
5. After each game, both teams confirm the winner.
6. When one team reaches 3 wins, the match is saved and Elo is updated automatically.

## Elo

Elo is updated after the bo5 series, not after each game.

Formula:

```text
E_i = 1 / (1 + 10^((R_j - R_i) / 400))
S_i = (w_i + 0.5) / (w_i + w_j + 1)
R'_i = R_i + 64 * (S_i - E_i)
```

Where:

- `R_i` is team rating before the match;
- `w_i` is the number of games won by team `i`;
- `E_i` is expected score;
- `S_i` is series score.

The final rating is rounded to the nearest integer and saved back to `nsl_teams.elo`.
