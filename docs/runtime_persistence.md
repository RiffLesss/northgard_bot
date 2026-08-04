# Persistent Discord workflows

Interactive workflow state is stored in the `runtime_states` table. The state is
JSON because Discord workflows contain small, evolving state machines and message
IDs rather than relational business entities.

The current implementation restores the 3v3 panel registry, ranked queue, casual
lobbies, occupied players, and active 3v3 draft state after a restart. The panel
and draft menu use stable Discord component IDs and are registered as persistent
views during bot startup. The 3v3 result-confirmation view also stores all votes
and its absolute deadline, so a restart does not reset the voting window.
Administrator dispute messages store the same match context and team votes, and
their resolution buttons are restored after a restart as well.
3v3 ready-checks additionally store the selected teams and source channels, so
an accepted check can resume matchmaking and enter the draft after a restart.
2v2 sessions store their players, score, current game and result-waiting phase.
On restart they are restored into the message listener; an in-progress game is
restarted from its current series score, while a result-waiting game continues
to accept `win @player`.
NSL scrim drafts and result confirmations use the same database-backed recovery,
including draft steps, fearless bans, magic cards, votes and deadlines. Bot admin
IDs are read from the existing `bot_admins` database table; no local JSON runtime
state is required.
NSL invitations and scheduled ready-checks also persist their messages, team
roles, participants and acceptance state; an already accepted workflow resumes
by entering the scrim draft.

Apply the migration before deploying:

```text
alembic upgrade head
```

The next persistence steps are to store the active 2v2/3v3/NSL draft state and
their message/channel IDs. A running `asyncio` task itself is never persisted;
after restart it must be rebuilt from the saved state and deadline.
