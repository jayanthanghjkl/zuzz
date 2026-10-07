# ZUZZ User Guide

## Getting Started
1. Run `python start.py` to launch ZUZZ.
2. Open `http://localhost:8080` in your browser.
3. Chat with ZUZZ, or use the Dashboard to monitor the brain.

## Understanding Trust Levels
- **L1**: Ask for everything (safest).
- **L2**: Auto-approves reading files, opening apps.
- **L3**: Auto-approves most actions.
- **L4**: Auto-approves all except immutable safety rules.
- **L5**: Full autonomy (still respects immutable rules).

ZUZZ cannot change its own trust level.

## Using Chat
- Type a message and press Enter.
- Shift+Enter for a newline.
- Use the routing level dropdown to force local or cloud models.

## Voice Commands
- Enable voice in Settings.
- Use the wake word "Hey ZUZZ" (if configured).
- Speak after the beep.

## Managing Memories
- Go to Memory page to view, search and add memories.
- Memories score by importance; high-scoring ones are promoted to disk.

## Creating Routines
- Enable Mirror Neurons in Settings.
- ZUZZ watches repeated actions and suggests macros after 5+ repetitions.
- Review and arm routines on the Routines page.

## Deploying Background Agents
- Use the Dashboard to see active agents.
- Cloud agents run on Colab and sync when you are back online.

## Troubleshooting
- **ZUZZ won't start**: check `storage/logs/` for errors.
- **Kill switch active**: type `RESET` in the stop panel.
- **Models not loading**: check VRAM usage on the Dashboard.
- **Cloud providers offline**: verify API keys in `.env`.

## FAQ
**Q: Does ZUZZ phone home?**
A: No. Everything runs locally unless you explicitly use cloud providers.

**Q: Can I lose data?**
A: Memories are backed up automatically. File deletions go to the recycle bin.

**Q: How do I update ZUZZ?**
A: Pull the latest code and restart. Settings and memories persist.
