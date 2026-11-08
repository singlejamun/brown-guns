# === In-Discord configuration ===
# Loaded into shared namespace by bot.py — do not run this file alone.
#
# Instead of ~50 environment variables you now only need DISCORD_TOKEN.
# Every channel / role / server ID is set from Discord with:
#
#   !config                          -> show what is set and what is still missing
#   !config set KEY value            -> set one value   (#channel, @role, an ID, or `here`)
#   !config set KEY @role1 @role2    -> role lists (STAFF_ROLE_IDS ...)
#   !config clear KEY               -> remove a saved value
#   !config bulk                     -> paste many `KEY = value` lines in one message
#   !config export                   -> sends `saved_config.json`. Put that file next to bot.py
#                                       before deploying anywhere (new host/account) and the
#                                       bot restores every setting from it automatically.
#   !config saveenv                  -> copy values that still come from env vars into the
#                                       database, so you can delete those env vars
#
# Saved values live in the `bot_settings` table (keys are prefixed `cfg:`), survive
# restarts, and OVERRIDE the same env var if both exist. Env vars still work as defaults,
# so an existing deployment keeps running unchanged.

CFG_PREFIX = "cfg:"
CONFIG_FILE = Path(__file__).resolve().parent / "saved_config.json"

# kind: channel | role | roles | guild | user | int | text
_CFG_SCHEMA: dict[str, tuple[str, str, bool]] = {}   # key -> (kind, description, required)
_CFG_GROUPS: list[tuple[str, list[str]]] = []


def _cfg_group(title: str, items: list[tuple[str, str, str, bool]]) -> None:
    keys = []
    for key, kind, desc, required in items:
        _CFG_SCHEMA[key] = (kind, desc, required)
        keys.append(key)
    _CFG_GROUPS.append((title, keys))


_cfg_group("Servers & owner", [
    ("GUILD_ID", "guild", "Main server (run the command inside it and use `here`)", True),
    ("CONTROL_GUILD_ID", "guild", "Private database/control server", True),
    ("VOICE_MAIN_GUILD_ID", "guild", "Server for voice moves (defaults to GUILD_ID)", False),
    ("BOT_OWNER_ID", "user", "Bot owner (auto-detected from the Discord application if unset)", False),
])

_cfg_group("Main server channels", [
    ("REGISTRATION_CHANNEL_ID", "channel", "Informal registration", True),
    ("ROLE_REQUEST_CHANNEL_ID", "channel", "Role requests", True),
    ("ROLE_REVIEW_CHANNEL_ID", "channel", "Role request review", True),
    ("EVENT_PING_CHANNEL_ID", "channel", "Event pings", True),
    ("STATE_REGISTER_CHANNEL_ID", "channel", "State register", True),
    ("COMPLAINT_CHANNEL_ID", "channel", "Complaint panel", True),
    ("COMPLAINT_REVIEW_CHANNEL_ID", "channel", "Complaint review", True),
    ("PUNISHMENT_BOARD_CHANNEL_ID", "channel", "Punishment board", True),
    ("PUNISHMENT_APPEAL_CHANNEL_ID", "channel", "Punishment appeals (optional)", False),
    ("POV_REVIEW_CHANNEL_ID", "channel", "POV review (optional)", False),
    ("MAIN_EVENT_PANEL_CHANNEL_ID", "channel", "Main event panel", True),
    ("WELCOME_CHANNEL_ID", "channel", "Welcome messages", True),
    ("RULES_CHANNEL_ID", "channel", "Rules", True),
    ("GENERAL_CHAT_CHANNEL_ID", "channel", "General chat", True),
    ("RP_TICKET_REMINDER_CHANNEL_ID", "channel", "RP ticket reminders (defaults to EVENT_PING)", False),
    ("SECURITY_ALERT_CHANNEL_ID", "channel", "Anti-hack alerts (optional)", False),
])

_cfg_group("Control server channels", [
    ("CONTROL_COMMAND_CHANNEL_ID", "channel", "Control commands", True),
    ("CONTROL_SERVER_LOG_CHANNEL_ID", "channel", "Server log", True),
    ("CONTROL_MEMBER_INFO_CHANNEL_ID", "channel", "Member info", True),
    ("CONTROL_COMPLAINT_LOG_CHANNEL_ID", "channel", "Complaint log", True),
    ("CONTROL_RANK_LOG_CHANNEL_ID", "channel", "Rank log", True),
    ("RP_TICKET_HC_LOG_CHANNEL_ID", "channel", "RP ticket HC log (defaults to server log)", False),
    ("FAMILY_LOG_CHANNEL_ID", "channel", "Family log (defaults to RP ticket HC log)", False),
    ("CONTROL_PANEL_CHANNEL_ID", "channel", "Voice panel channel (optional)", False),
])

_cfg_group("Voice channels", [
    ("EVENT_VC_ID", "channel", "Event voice channel", False),
    ("TALK_2_VC_ID", "channel", "Talk 2 voice channel", False),
])

_cfg_group("Roles", [
    ("INFORMAL_ROSTER_ROLE_ID", "role", "Informal roster", True),
    ("TURFER_ROLE_ID", "role", "Turfer", True),
    ("FAMILY_MEMBER_ROLE_ID", "role", "Family member", True),
    ("STATE_ROSTER_ROLE_ID", "role", "State roster (optional)", False),
    ("STATE_LEADER_ROLE_ID", "role", "State leader", True),
    ("ADVISER_ROLE_ID", "role", "Adviser (optional)", False),
    ("SECURITY_ALERT_ROLE_ID", "role", "Pinged on security alerts (optional)", False),
    ("WARN_1_ROLE_ID", "role", "Warning 1 role (optional — leave empty, warnings are tracked by the bot only)", False),
    ("WARN_2_ROLE_ID", "role", "Warning 2 role (optional — leave empty, warnings are tracked by the bot only)", False),
    ("WARN_3_ROLE_ID", "role", "Warning 3 role (optional — leave empty, warnings are tracked by the bot only)", False),
])

_cfg_group("Rank roles", [
    (f"RANK_{n}_ROLE_ID", "role",
     f"Rank {n}" + (f" ({RANK_NAMES[n]})" if n in RANK_NAMES else ""), True)
    for n in range(1, 11)
])

_cfg_group("Permission role lists", [
    ("STAFF_ROLE_IDS", "roles", "Staff roles (several allowed)", False),
    ("HIGH_COMMAND_ROLE_IDS", "roles", "High command roles (several allowed)", False),
    ("CONTROL_ADMIN_ROLE_IDS", "roles", "Control admin roles (several allowed)", False),
    ("CONTROL_PANEL_ROLE_IDS", "roles", "Extra voice-panel roles (several allowed)", False),
])

_cfg_group("Other", [
    ("SECURITY_TIMEOUT_MINUTES", "int", "Timeout length for hacked accounts", False),
    ("ALT_ACCOUNT_MAX_DAYS", "int", "Accounts younger than this are flagged as alts", False),
    ("WELCOME_BANNER_URL", "text", "Welcome banner image URL", False),
])


# ---------------------------------------------------------------------------
# reading / writing the live values
# ---------------------------------------------------------------------------
_CFG_SET_TARGETS = {
    "STAFF_ROLE_IDS": STAFF_ROLE_IDS,
    "HIGH_COMMAND_ROLE_IDS": HIGH_COMMAND_ROLE_IDS,
    "CONTROL_ADMIN_ROLE_IDS": CONTROL_ADMIN_ROLE_IDS,
    "CONTROL_PANEL_ROLE_IDS": CONTROL_PANEL_ROLE_IDS,
}

# Derived settings: used only while their own value is not set.
_CFG_FALLBACKS = {
    "RP_TICKET_HC_LOG_CHANNEL_ID": "CONTROL_SERVER_LOG_CHANNEL_ID",
    "FAMILY_LOG_CHANNEL_ID": "RP_TICKET_HC_LOG_CHANNEL_ID",
    "RP_TICKET_REMINDER_CHANNEL_ID": "EVENT_PING_CHANNEL_ID",
    "VOICE_MAIN_GUILD_ID": "GUILD_ID",
}
# Keys the user explicitly set (env or saved); the rest follow their fallback.
_cfg_explicit: set[str] = set()


def cfg_get(key: str):
    ns = globals()
    if key in _CFG_SET_TARGETS:
        return set(_CFG_SET_TARGETS[key])
    if key.startswith("WARN_") and key.endswith("_ROLE_ID"):
        return WARNING_ROLE_IDS.get(int(key.split("_")[1]), 0)
    if key.startswith("RANK_") and key.endswith("_ROLE_ID"):
        n = int(key.split("_")[1])
        if n in RANK_ROLE_IDS:
            return RANK_ROLE_IDS[n]
    return ns.get(key, 0)


def cfg_apply(key: str, value) -> None:
    """Write a value into the live globals the rest of the bot reads."""
    ns = globals()
    if key in _CFG_SET_TARGETS:
        target = _CFG_SET_TARGETS[key]
        target.clear()
        target.update(int(v) for v in value)
    elif key.startswith("WARN_") and key.endswith("_ROLE_ID"):
        WARNING_ROLE_IDS[int(key.split("_")[1])] = int(value)
    elif key.startswith("RANK_") and key.endswith("_ROLE_ID") and int(key.split("_")[1]) <= 8:
        RANK_ROLE_IDS[int(key.split("_")[1])] = int(value)
    else:
        ns[key] = value


def cfg_recompute_fallbacks() -> None:
    ns = globals()
    for key in ("VOICE_MAIN_GUILD_ID", "RP_TICKET_HC_LOG_CHANNEL_ID",
                "FAMILY_LOG_CHANNEL_ID", "RP_TICKET_REMINDER_CHANNEL_ID"):
        if key not in _cfg_explicit:
            ns[key] = ns.get(_CFG_FALLBACKS[key], 0)


def _cfg_empty(value) -> bool:
    return not value


def _cfg_encode(kind: str, value) -> str:
    if kind == "roles":
        return ",".join(str(v) for v in sorted(value))
    return str(value)


def _cfg_decode(kind: str, raw: str):
    if kind == "roles":
        return {int(v) for v in raw.split(",") if v.strip().isdigit()}
    if kind == "text":
        return raw
    if raw.strip().lstrip("-").isdigit():
        return int(raw)
    return None


def load_saved_config() -> None:
    """Synchronous; runs once in bot.py after every file is loaded, BEFORE the bot starts.
    Saved values override env vars."""
    # Anything non-empty right now came from an env var (or a built-in default).
    for key, (kind, _desc, _req) in _CFG_SCHEMA.items():
        if key in _CFG_FALLBACKS:
            if os.getenv(key, "").strip():
                _cfg_explicit.add(key)
        elif not _cfg_empty(cfg_get(key)):
            _cfg_explicit.add(key)

    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM bot_settings WHERE key LIKE ?", (CFG_PREFIX + "%",)
            ).fetchall()
    except Exception:
        log.exception("Could not read saved configuration.")
        rows = []

    loaded = 0
    for row in rows:
        key = str(row["key"])[len(CFG_PREFIX):]
        schema = _CFG_SCHEMA.get(key)
        if schema is None:
            continue
        value = _cfg_decode(schema[0], str(row["value"]))
        if value is None:
            continue
        cfg_apply(key, value)
        _cfg_explicit.add(key)
        loaded += 1

    # saved_config.json (made by `!config export`) fills in anything the database
    # does not have yet — this is how settings survive a move to a new host/account.
    db_keys = {str(r["key"])[len(CFG_PREFIX):] for r in rows}
    from_file = 0
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            with db.connect() as conn:
                for key, raw in data.items():
                    schema = _CFG_SCHEMA.get(key)
                    if schema is None or key in db_keys:
                        continue
                    value = _cfg_decode(schema[0], str(raw))
                    if value is None or _cfg_empty(value):
                        continue
                    cfg_apply(key, value)
                    _cfg_explicit.add(key)
                    conn.execute(
                        "INSERT OR IGNORE INTO bot_settings(key, value) VALUES (?, ?)",
                        (CFG_PREFIX + key, str(raw)),
                    )
                    from_file += 1
        except Exception:
            log.exception("Could not read %s.", CONFIG_FILE.name)

    cfg_recompute_fallbacks()
    log.info("Configuration: %d value(s) from the database, %d from %s.",
             loaded, from_file, CONFIG_FILE.name)


def cfg_missing_required() -> list[str]:
    missing = []
    if not TOKEN:
        missing.append("DISCORD_TOKEN (environment variable)")
    for key, (_kind, _desc, required) in _CFG_SCHEMA.items():
        if required and _cfg_empty(cfg_get(key)):
            missing.append(key)
    return missing


async def detect_bot_owner() -> None:
    """If BOT_OWNER_ID is not set, use the owner of the Discord application."""
    global BOT_OWNER_ID
    if BOT_OWNER_ID > 0:
        return
    try:
        info = await bot.application_info()
        owner = info.team.owner if info.team else info.owner
        if owner:
            BOT_OWNER_ID = owner.id
            log.info("BOT_OWNER_ID auto-detected: %s", BOT_OWNER_ID)
    except Exception:
        log.exception("Could not auto-detect the bot owner.")


# ---------------------------------------------------------------------------
# parsing user input
# ---------------------------------------------------------------------------
_ID_RE = re.compile(r"\d{15,21}")


def cfg_parse(kind: str, text: str, ctx: commands.Context | None = None):
    """Returns the parsed value or raises ValueError with a friendly message."""
    text = text.strip()
    if kind == "text":
        return text
    if kind == "int":
        if not text.lstrip("-").isdigit():
            raise ValueError("Give a number, e.g. `180`.")
        return int(text)

    ids = [int(m) for m in _ID_RE.findall(text)]
    lowered = text.lower()

    if kind == "roles":
        if not ids:
            raise ValueError("Mention one or more roles (or paste their IDs).")
        return set(ids)

    if kind == "guild":
        if lowered in {"here", "this"} and ctx is not None and ctx.guild is not None:
            return ctx.guild.id
    elif kind == "channel":
        if lowered in {"here", "this"} and ctx is not None:
            return ctx.channel.id
    elif kind == "user":
        if lowered in {"me", "myself"} and ctx is not None:
            return ctx.author.id

    if len(ids) != 1:
        hint = {"guild": "a server ID or `here`", "channel": "a #channel or its ID (or `here`)",
                "role": "a @role or its ID", "user": "a @user, their ID, or `me`"}[kind]
        raise ValueError(f"Give {hint}.")
    return ids[0]


def cfg_describe(kind: str, value) -> str:
    if kind == "roles":
        return " ".join(f"<@&{v}>" for v in sorted(value)) if value else "—"
    if _cfg_empty(value):
        return "—"
    if kind == "channel":
        return f"<#{value}>"
    if kind == "role":
        return f"<@&{value}>"
    if kind == "user":
        return f"<@{value}>"
    return f"`{value}`"


def _cfg_can_manage(member: discord.Member) -> bool:
    if BOT_OWNER_ID > 0 and member.id == BOT_OWNER_ID:
        return True
    # Before an owner is known, server admins may configure the bot.
    perms = member.guild_permissions
    return perms.administrator or (BOT_OWNER_ID <= 0 and perms.manage_guild)


def _cfg_find_key(name: str) -> str | None:
    name = name.strip().upper().replace("-", "_")
    if name in _CFG_SCHEMA:
        return name
    if name + "_ID" in _CFG_SCHEMA:
        return name + "_ID"
    if name + "_IDS" in _CFG_SCHEMA:
        return name + "_IDS"
    return None


async def _cfg_save(key: str, value) -> None:
    kind = _CFG_SCHEMA[key][0]
    await set_setting(CFG_PREFIX + key, _cfg_encode(kind, value))
    cfg_apply(key, value)
    _cfg_explicit.add(key)
    cfg_recompute_fallbacks()


async def _cfg_clear(key: str) -> None:
    await delete_setting(CFG_PREFIX + key)
    kind = _CFG_SCHEMA[key][0]
    cfg_apply(key, set() if kind == "roles" else ("" if kind == "text" else 0))
    _cfg_explicit.discard(key)
    cfg_recompute_fallbacks()


def _cfg_chunks(lines: list[str], limit: int = 1800) -> list[str]:
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)
    return chunks


_CFG_HELP = (
    "**Bot configuration**\n"
    "`!config` — show what is set / missing\n"
    "`!config set KEY value` — e.g. `!config set REGISTRATION_CHANNEL_ID #registration`, "
    "`!config set TURFER_ROLE_ID @Turfer`, `!config set GUILD_ID here`\n"
    "`!config set STAFF_ROLE_IDS @Staff @Mods` — role lists take several roles\n"
    "`!config clear KEY` — remove a saved value\n"
    "`!config bulk` — put `KEY = value` lines under the command to set many at once\n"
    "`!config export` — get `saved_config.json` to keep with the bot files (for moving host)\n"
    "`!config saveenv` — save values currently coming from env vars into the database\n"
    "You can leave out the `_ID` ending: `!config set registration_channel #reg`."
)


@bot.command(name="config", aliases=["settings"])
async def config_command(ctx: commands.Context, *, args: str = "") -> None:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return
    if not _cfg_can_manage(ctx.author):
        await ctx.reply("❌ Only the bot owner or a server administrator can change the configuration.",
                        mention_author=False)
        return

    quiet = discord.AllowedMentions.none()
    head = args.strip().split(None, 1)
    action = head[0].lower() if head else ""
    rest = head[1].strip() if len(head) > 1 else ""

    # ---------------- status
    if action in {"", "status", "show", "list"}:
        missing = cfg_missing_required()
        lines = []
        if missing:
            lines.append(f"⚠️ **Still missing ({len(missing)}):** " + ", ".join(f"`{m}`" for m in missing))
        else:
            lines.append("✅ **All required settings are set.**")
        lines.append("")
        for title, keys in _CFG_GROUPS:
            lines.append(f"**{title}**")
            for key in keys:
                kind, desc, required = _CFG_SCHEMA[key]
                value = cfg_get(key)
                mark = "✅" if not _cfg_empty(value) else ("❌" if required else "➖")
                lines.append(f"{mark} `{key}` {cfg_describe(kind, value)}")
            lines.append("")
        lines.append("Use `!config help` for how to set values.")
        for chunk in _cfg_chunks(lines):
            await ctx.send(chunk, allowed_mentions=quiet)
        return

    if action == "help":
        await ctx.reply(_CFG_HELP, mention_author=False)
        return

    # ---------------- set
    if action == "set":
        name, _, value_text = rest.partition(" ")
        key = _cfg_find_key(name)
        if key is None or not value_text.strip():
            await ctx.reply("❌ Usage: `!config set KEY value`. See `!config help` for the key names.",
                            mention_author=False)
            return
        kind = _CFG_SCHEMA[key][0]
        try:
            value = cfg_parse(kind, value_text, ctx)
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}", mention_author=False)
            return
        await _cfg_save(key, value)
        note = ""
        if kind == "channel" and bot.get_channel(value) is None:
            note = "\n⚠️ I can't see that channel right now (wrong ID, or I lack access)."
        await ctx.reply(f"✅ `{key}` → {cfg_describe(kind, value)}{note}",
                        mention_author=False, allowed_mentions=quiet)
        return

    # ---------------- clear
    if action in {"clear", "remove", "unset", "reset"}:
        key = _cfg_find_key(rest)
        if key is None:
            await ctx.reply("❌ Unknown key. See `!config` for the list.", mention_author=False)
            return
        await _cfg_clear(key)
        await ctx.reply(f"✅ `{key}` cleared.", mention_author=False)
        return

    # ---------------- bulk
    if action == "bulk":
        done, failed = [], []
        for raw_line in rest.splitlines():
            line = raw_line.strip().strip("`")
            if not line or line.startswith("#") or ("=" not in line and ":" not in line):
                continue
            name, value_text = re.split(r"\s*[=:]\s*", line, maxsplit=1)
            key = _cfg_find_key(name)
            if key is None:
                failed.append(f"`{name.strip()}` (unknown key)")
                continue
            try:
                value = cfg_parse(_CFG_SCHEMA[key][0], value_text, ctx)
            except ValueError:
                failed.append(f"`{key}` (bad value)")
                continue
            await _cfg_save(key, value)
            done.append(key)
        text = f"✅ Saved {len(done)} setting(s)."
        if failed:
            text += "\n⚠️ Skipped: " + ", ".join(failed)
        if not done and not failed:
            text = "ℹ️ Put lines like `REGISTRATION_CHANNEL_ID = 123456789012345678` under `!config bulk`."
        await ctx.reply(text, mention_author=False)
        return

    # ---------------- export
    if action == "export":
        data = {}
        for key, (kind, _d, _r) in _CFG_SCHEMA.items():
            value = cfg_get(key)
            if _cfg_empty(value):
                continue
            if key in _CFG_FALLBACKS and key not in _cfg_explicit:
                continue
            data[key] = _cfg_encode(kind, value)
        payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        text = (
            f"✅ {len(data)} settings exported (no token inside, only IDs).\n"
            "Put **saved_config.json** in the bot folder next to `bot.py` before you deploy. "
            "On start the bot loads it automatically."
        )
        def _file():
            return discord.File(io.BytesIO(payload), filename="saved_config.json")

        try:
            await ctx.author.send(text, file=_file())
            await ctx.reply("📬 I sent the file to your DMs.", mention_author=False)
        except Exception:
            try:
                await ctx.reply(text, file=_file(), mention_author=False)
            except Exception:
                # No DM and no Attach Files permission: send the content as text instead.
                await ctx.reply(
                    "⚠️ I couldn't send a file (open your DMs or give me **Attach Files**). "
                    "Copy the text below into a file named `saved_config.json`:",
                    mention_author=False,
                )
                for chunk in _cfg_chunks(payload.decode("utf-8").splitlines(), 1700):
                    await ctx.send(f"```json\n{chunk}```")
        return

    # ---------------- saveenv
    if action == "saveenv":
        saved = 0
        for key, (kind, _d, _r) in _CFG_SCHEMA.items():
            value = cfg_get(key)
            if _cfg_empty(value):
                continue
            if key in {"VOICE_MAIN_GUILD_ID", "RP_TICKET_HC_LOG_CHANNEL_ID",
                       "FAMILY_LOG_CHANNEL_ID", "RP_TICKET_REMINDER_CHANNEL_ID"} \
                    and key not in _cfg_explicit:
                continue  # these only follow a fallback; don't freeze it
            await set_setting(CFG_PREFIX + key, _cfg_encode(kind, value))
            saved += 1
        await ctx.reply(
            f"✅ Saved {saved} value(s) into the database. You can now delete those environment "
            "variables (keep `DISCORD_TOKEN`).",
            mention_author=False,
        )
        return

    await ctx.reply(_CFG_HELP, mention_author=False)


@bot.command(name="configexport", aliases=["confighexport"])
async def config_export_command(ctx: commands.Context) -> None:
    await config_command.callback(ctx, args="export")
