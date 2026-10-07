# ZUZZ API Reference

## Base URL
`http://localhost:8080`

## Chat
- `POST /api/chat` — Send a message. Returns `{response, actions}`.
- `POST /api/chat/confirm` — Confirm a pending action.

## Brain
- `GET /api/health` — Liveness probe.
- `GET /api/brain/status` — Heartbeat, thalamus, scheduler, kill switch.
- `GET /api/brain/activity` — Recent activity log.
- `GET /api/providers` — Cloud provider health.

## Safety
- `GET /api/safety/trust` — Current trust level and matrix.
- `POST /api/safety/trust` — Change trust level (requires confirmation).
- `POST /api/safety/trust/preview` — Preview a trust change.
- `GET /api/safety/rules` — Immutable rules.
- `POST /api/safety/check` — Dry-run an action.

## Files
- `GET /api/files/list` — List files.
- `POST /api/files/read` — Read a file.
- `POST /api/files/write` — Write a file.
- `POST /api/files/delete` — Delete (moves to trash).
- `POST /api/files/trash/restore` — Restore from trash.

## Dashboard
- `GET /api/dashboard` — Full dashboard payload.

## Settings
- `GET /api/settings` — Effective configuration.
- `POST /api/settings` — Update editable settings.

## Plugins
- `GET /api/plugins` — Installed plugins.
- `POST /api/plugins/scan` — Scan for new plugins.

## Kill Switch
- `GET /api/kill-switch` — Status.
- `POST /api/kill-switch/stop` — Engage stop.
- `POST /api/kill-switch/reset` — Reset (requires typed word).

## Memory
- `GET /api/memory/profile` — Memory profile.
- `POST /api/memory/remember` — Store a memory.
- `POST /api/memory/recall` — Search memories.
- `GET /api/memory/history` — All memories.
- `POST /api/memory/consolidate` — Promote to disk.
- `DELETE /api/memory/forget` — Erase memories.

## Personality
- `GET /api/personality/settings` — Current personality.
- `POST /api/personality/settings` — Update personality.
- `POST /api/personality/detect-mood` — Detect mood from text.

## Routines / Habits
- `GET /api/habits` — Routines list.
- `POST /api/habits/routine` — Create routine.
- `POST /api/habits/arm` — Arm a routine.

## Error Codes
- `400` — Bad request / validation error.
- `403` — Permission denied.
- `404` — Not found.
- `409` — Kill switch active.
- `500` — Internal server error.
- `503` — Service unavailable (model not loaded).
