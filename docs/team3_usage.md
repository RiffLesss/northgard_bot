# 3v3 Modes

## 3v3 Panel

A server admin sends this command in the target channel:

```text
/team3_panel
```

The bot sends one message with buttons:

```text
Casual 3v3
Ranked 3v3
Ranked wide
Leave queue
```

The same message shows current lobby/queue counters and updates when players join or leave.

## Casual 3v3

A player presses `Casual 3v3`.

Rules:

- solo queue only;
- parties/pre-made groups are disabled for casual;
- when 6 players are gathered, the bot randomly splits them into two teams;
- all 6 players must be in any server voice channel;
- after a match is found, the bot creates a temporary text channel and starts a ready-check.

If someone is not in voice, that player is removed from the lobby and the others keep waiting.

## Ranked 3v3

A player presses `Ranked 3v3`.

The bot searches for 6 players within the normal rating spread:

```text
max_rating - min_rating <= 300
```

When a match is found, the bot creates the most balanced teams by total rating.

## Ranked Wide

A player presses `Ranked wide`.

This is matchmaking with a wider rating spread. A match without the normal rating limit is created only if all 6 selected players are searching wide.

## Search And Active Matches

A player can be in multiple searches at the same time, for example casual and ranked.

But if a player is already in an active 3v3 match, they cannot join a new search until the current match is finished or cancelled.

The bot uses each player's personal blacklist when forming teams. If a player blacklisted someone, the bot will not place them on the same team.

In ranked, the bot tries another valid group of 6 or another split. In casual, if the current 6 players cannot be split into two teams without blacklist conflicts, the lobby stays full but the match does not start until someone leaves or updates their blacklist.

## Ready-Check

Ready-check happens in the temporary private text channel for the found match.

Buttons:

```text
Accept
Decline
```

All 6 players must press `Accept` within 60 seconds.

If ready-check is not accepted:

- the match is cancelled;
- players who accepted remain in the lobby/queue;
- players who did not answer or declined leave the lobby/queue;
- the temporary ready-check channel is deleted.

## Draft

After ready-check, the bot starts a draft message with a select menu.

Clear/eco pools are loaded from the `clans` table. Enabled clans with `is_clear=true` go to the clear pool, all other enabled clans go to the eco pool.

Any player from the team whose turn is shown in the phase can choose:

```text
Phase: Team A: ban clear
```

Each draft action has a 2-minute timer. The draft message shows the remaining time and updates roughly every 5 seconds.

If a team does not choose a clan within 2 minutes, the bot randomly chooses one of the available clans and moves to the next step.

Draft order:

```text
A ban clear - B ban clear
B pick clear - A pick clear

B ban eco - A ban eco
A ban eco - B ban eco
B pick eco - A pick eco

B ban eco - A ban eco
A pick eco - B pick eco
```

Rules:

- no duplicate clans inside one team;
- banned clans cannot be picked;
- bans are shared;
- one team cannot pick 2 clear clans.

In bo3/bo5, draft sides swap every game.

## Result Confirmation

After draft, the bot sends buttons:

```text
Team A won
Team B won
```

At least 2 players from Team A and at least 2 players from Team B must confirm the same winner.

Confirmation limits:

- bo1: 2 hours;
- bo3/bo5: 24 hours.

If time runs out, the match is cancelled and temporary channels/roles are deleted.

If teams choose different winners, the bot sends a disputed result to admin channel `1520167921194766518` with `@here`. Admins get buttons to choose the winner. After an admin decision, the result is applied to the current game. If the series is not finished, the next game continues in the original match channel.

## Match Finish

After final confirmation:

- casual records the result without rating changes;
- ranked records the result and updates rating;
- tournament records the series result;
- the bot deletes the temporary match text channel;
- casual/ranked temporary voice channels are deleted;
- ranked temporary team roles are deleted.

## Temporary Channels

For casual and ranked, the bot creates a temporary private text channel:

```text
3v3-match-123
```

It contains:

- ready-check;
- draft;
- result confirmation;
- bo3/bo5 series continuation;
- final winner message.

For casual and ranked, the bot also creates temporary team voice channels:

```text
3v3 Team A #123
3v3 Team B #123
```

In casual, voice channels are open for everyone on the server. In ranked, voice channels are private for the teams.

## Tournament 3v3

The organizer sends:

```text
/tournament_3v3_start @team_a_role @team_b_role bo1
/tournament_3v3_start @team_a_role @team_b_role bo3
/tournament_3v3_start @team_a_role @team_b_role bo5
```

Each team role must contain exactly 3 players.

Tournament mode does not create roles or voice channels. It handles draft, result confirmation, and series winner recording.
