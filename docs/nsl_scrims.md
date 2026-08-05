# NSL Scrims

## Team Setup

Tournament organizers create a team with:

```text
/add_nsl_team team_name player1 player2 player3 player4
```

`player4` is optional. The bot creates:

- a Discord role named after the team;
- a private text channel for the team;
- a private voice channel for the team.

All team channels are created in category `1526212599820062982`.
All selected players must already be registered with `/register`.

Team metadata is stored in PostgreSQL:

- `nsl_teams.team_name`;
- `nsl_teams.elo`;
- Discord role/channel ids;
- team membership in `nsl_team_members`.

## Scrim Flow

A player from a registered NSL team can challenge another registered NSL team:

```text
/scrim @team_role
```

The bot creates a private scrim text channel for both team roles. At least one player from the challenged team must accept the invite within 2 minutes.

If nobody accepts, the invite expires. If the invite is accepted, a bo5 scrim starts in that channel.

## Draft Order

Teams swap draft sides every game.
At the start of the series, the team with the higher Elo is assigned side A.
If Elo is equal, the initial side is selected randomly.

```text
A ban clear
B ban clear
B ban eco
A ban eco
A pick clear
B pick clear
B pick eco
A pick eco
A ban eco
B ban eco
A pick eco
B pick eco
```

Each draft action has a 2 minute timer.

## Fearless Eco

Eco picks are fearless within the series. A team cannot pick the same eco clan in multiple games of the same bo5.

## Magic Cards

Each team has one magic card per series.

A magic card can be used during that team's pick turn to revert one ban made by the opposing team. Magic cards cannot revert fearless restrictions.

## Results

After each draft, the bot asks teams to confirm the game winner. One vote from each team for the same winner confirms the result.

The scrim ends when one team reaches 3 game wins.
