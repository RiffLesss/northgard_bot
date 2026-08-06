# NSL team draft statistics

The draft history for NSL scrims and official matches is stored in
`nsl_draft_games` and `nsl_draft_actions`. Apply migration `0009` before using
the report:

```text
alembic upgrade head
```

Run the report with the team's ID from `nsl_teams`:

```text
python scripts/nsl_team_stats.py <team_id>
```

The script reads `DATABASE_URL`, counts clear and eco bans, full three-clan
pick combinations, mirrored picks, and combinations the team lost against.
Reverted actions are excluded from the statistics.
