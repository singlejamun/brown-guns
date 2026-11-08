# Grand RP Family Bot (Organized)

The original single `bot.py` (~13k lines) has been split into folders by system.
Logic is unchanged. Files are loaded into one shared namespace at startup so all
cross-references still work exactly like the monolith.

## Structure

```
organized_bot/
├── bot.py                      # Entry point (run this)
├── config/
│   └── settings.py             # Env vars, role/channel IDs, constants
├── core/
│   ├── theme.py                # HUD theme & embed helpers
│   ├── utils.py                # Shared helpers (staff checks, logging, etc.)
│   ├── database.py             # SQLite Database class + `db`
│   └── bot_class.py            # FamilyBot class + `bot` instance
├── systems/
│   ├── rp_list.py              # !rplist payout tracking
│   ├── registration.py         # Informal registration
│   ├── role_request.py         # Role request intake / accept / deny
│   ├── punishment.py           # Warnings, board, appeals
│   ├── depwo.py                # Pending work reminders
│   ├── rank_request.py         # Rank request V2
│   ├── complaints.py           # Complaint system
│   ├── system_control.py       # System on/off + RP ticket reminders
│   ├── events_system.py        # State / family events
│   ├── voice_control.py        # Voice control panel
│   ├── juice.py                # Juice panel
│   ├── startup.py              # setup_hook (restore views, start tasks)
│   ├── data_setup.py           # Database server auto-setup + many events
│   ├── control_commands.py     # Private control-server commands
│   ├── general.py              # General commands
│   ├── security.py             # !security lockdown + anti-hack detection
│   ├── lol.py                  # Continuous VC dragger + !lolaccess permission
│   ├── error_handler.py        # Command error handler
│   ├── recovery.py             # Owner role recovery
│   └── validation.py           # Config validation
├── security_patterns/          # Put sample scam/hack messages here (.txt)
│   ├── README.txt
│   ├── mrbeast_scam.txt
│   └── generic_hack.txt
├── requirements.txt
├── Procfile
└── railway.json
```

### Anti-hack / compromised account detection

When a member posts a message matching patterns in `security_patterns/` (or built-in MrBeast-style scam phrases):

1. Message is deleted
2. All removable roles are stripped
3. Member is timed out for `SECURITY_TIMEOUT_MINUTES` (default 180)
4. Alert is posted in `SECURITY_ALERT_CHANNEL_ID` (pings `SECURITY_ALERT_ROLE_ID`)

**Detection sources:**
- Message text + embeds
- Image + suspicious caption (simple)
- OCR of attached screenshots (strong) — requires system package `tesseract-ocr`

**Patterns folder (`security_patterns/`):**
- `.txt` files → one phrase per line
- Screenshots (`.png` / `.jpg` / …) → OCR’d on load into text patterns
- After adding files: `!reloadpatterns`

**New env vars (optional):**
- `SECURITY_ALERT_CHANNEL_ID` — channel for the alert message
- `SECURITY_ALERT_ROLE_ID` — role to @ping in the alert
- `SECURITY_TIMEOUT_MINUTES` — timeout length (default 180)

**Commands:**
- `!testhack <text>` — test text (owner)
- `!testhack` + attach image — test OCR (owner)
- `!reloadpatterns` — reload text + image patterns (owner)
- `!testrpreminder` — post a test RP ticket reminder (staff)
- System toggle: `!systemoff hack_detection` / `!systemon hack_detection`

**Deploy note (OCR):**  
Host needs Tesseract installed, e.g. Debian/Ubuntu: `apt install tesseract-ocr`  
Python deps: `Pillow` and `pytesseract` (already in `requirements.txt`).


## Quick setup (only ONE environment variable)

The only variable you must set is `DISCORD_TOKEN` (see `.env.example`).
Everything else — channels, roles, servers, owner — is set from Discord and saved in the database:

```
!config                                  show what is set / missing
!config set GUILD_ID here                run inside your main server
!config set CONTROL_GUILD_ID here        run inside your control server
!config set REGISTRATION_CHANNEL_ID #registration
!config set TURFER_ROLE_ID @Turfer
!config set STAFF_ROLE_IDS @Staff @Mods  (role lists take several roles)
!config clear KEY                        remove a saved value
!config bulk                             paste many `KEY = value` lines at once
!config saveenv                          copy your old env-var values into the database
```

- `_ID` can be left out and case is ignored: `!config set registration_channel #reg`.
- Who can use it: the bot owner, or a server administrator. The owner is auto-detected
  from the Discord application, so `BOT_OWNER_ID` is no longer needed.
- Saved values override env vars; old env vars still work as defaults, so existing
  deployments keep running. Run `!config saveenv`, then delete the old env vars.
- The bot now starts even when settings are missing (it only logs what is missing).
- Most settings apply instantly. Panels and schedulers are restored at startup, so
  restart the bot once after finishing the first-time setup.
- The database must be on persistent storage (Railway volume) or saved settings are lost on redeploy.

## How to run

```bash
pip install -r requirements.txt
# Set DISCORD_TOKEN, then do the rest with !config in Discord
python bot.py
```

Railway / Procfile still use: `python bot.py`

## Notes

- Do **not** run individual system files alone; they expect the shared namespace.
- To change a feature, edit the matching file under `systems/` or `core/`.
- Adding a new system: create `systems/your_feature.py` and add its path to `LOAD_ORDER` in `bot.py`.


## Voice Control Panel — setup

Environment variables (Railway → Variables):

| Variable | Needed for | Notes |
|---|---|---|
| `GUILD_ID` | everything | Main server |
| `VOICE_MAIN_GUILD_ID` | voice moves | Optional — falls back to `GUILD_ID` (empty value is fine) |
| `EVENT_VC_ID` | Drag to Event VC, Event to Talk 2 | Voice/Stage channel ID in the **main** server |
| `TALK_2_VC_ID` | Event to Talk 2 | Voice/Stage channel ID in the **main** server |
| `CONTROL_PANEL_CHANNEL_ID` | where the panel is posted | Optional — otherwise the `#voice-panel` made by `!startdata` is used |
| `CONTROL_PANEL_ROLE_IDS` | extra allowed roles | Optional, comma-separated. Control admins / owner / Move Members holders are always allowed |

The bot needs **View Channel, Connect, Move Members** on the voice channels, and
**View Channel, Read Message History, Send Messages, Embed Links** in the panel channel.

Commands: `!voicepanel` (refresh/create) · `!voicepanel here` (create in the current channel)

## Changelog (this version)

- **Jailer role** (`!jailer @role`): only members with this role can use `!punish`, `!remove`,
  Remove Strike, review punishment appeals, review complaints and review POV requests.
  `!jailer` shows it, `!jailer off` clears it. Saved in the database (optional env default
  `JAILER_ROLE_ID`). Until a role is set, high command is used so nobody gets locked out.
- **POV requests**: new 🎥 *POV Request* button on the complaint panel (Your ID, Suspect ID, Event,
  Date/Time optional, Reason). Jailers Accept/Reject in the POV review channel
  (`POV_REVIEW_CHANNEL_ID`, falls back to `COMPLAINT_REVIEW_CHANNEL_ID`). On Accept the bot finds the
  suspect by ID (nickname `Name | ID` or an accepted rank request) and DMs him: 4 hours to send a
  video link. If he can't be found the post gets **Member** (type his username) and **Revoke**
  buttons. When the link arrives the post is edited with the link and **Approve / Reject**; Approve
  DMs the link to the member who asked. Open requests expire after 4 hours and survive restarts.
- **Bug fixes**: warning roles are re-applied when a warned member leaves and rejoins; two reviewers
  can no longer both resolve the same complaint (atomic claim); a complaint whose review post fails to
  send is no longer saved as a ghost row; the complaint panel that is already posted is refreshed on
  startup so new buttons appear without `!complaintpanel`.

- **Voice panel**: fixed `get_main_voice_channel` being overwritten by `control_commands.py`
  (same function name in the shared namespace) which made the buttons reject valid channels.
  Clear error messages for every misconfiguration, bot-permission pre-check, dropdowns show the
  selection, >25 channel servers keep Event/Talk/busy channels, panel setup can't crash startup.
- **Registration**: Participate / Cancel now defer immediately (fixes `404 Unknown interaction`
  under load) and a missing `return` after a role-add failure was added.
- **OCR**: Tesseract is checked once; no more traceback per screenshot. `nixpacks.toml` installs
  the `tesseract` binary on Railway.
- **Log channels**: control-server log posts are queued per channel (fixes 429 rate-limit floods).
- **State Register**: a deleted panel message is a warning, not an ERROR traceback.
- **Startup**: rank/complaint/voice panel restore failures are logged and skipped instead of
  aborting startup.
