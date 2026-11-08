# === RP list ===
# Source lines 1167-1406 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# RP LIST — PAYOUT TRACKING (!rplist)
# =========================================================

RP_LIST_MENTION_RE = re.compile(r"<@!?(\d+)>")
RP_LIST_MAX_MEMBERS = 25


def initialize_rp_list_schema() -> None:
    with db.connect() as database:
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_list_panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                template TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rp_list_entries (
                panel_id INTEGER NOT NULL,
                line_index INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                paid INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(panel_id, user_id),
                FOREIGN KEY(panel_id) REFERENCES rp_list_panels(id) ON DELETE CASCADE
            );
            """
        )
        database.commit()


initialize_rp_list_schema()


async def rp_list_panel_row(panel_id: int) -> sqlite3.Row | None:
    return await db.fetchone(
        "SELECT * FROM rp_list_panels WHERE id = ?",
        (panel_id,),
    )


async def rp_list_entries(panel_id: int) -> list[sqlite3.Row]:
    return await db.fetchall(
        "SELECT * FROM rp_list_entries WHERE panel_id = ?",
        (panel_id,),
    )


async def rp_list_render_text(panel_id: int) -> str:
    """Rebuilds the exact original message text, adding a ✅ at the end
    of any winner's line once they've been marked paid."""
    panel = await rp_list_panel_row(panel_id)

    if panel is None:
        return "❌ This RP list no longer exists."

    lines = str(panel["template"]).split("\n")
    entries = await rp_list_entries(panel_id)

    for entry in entries:
        if not entry["paid"]:
            continue
        idx = int(entry["line_index"])
        if 0 <= idx < len(lines) and not lines[idx].rstrip().endswith("✅"):
            lines[idx] = lines[idx].rstrip() + " ✅"

    return "\n".join(lines)


async def update_rp_list_message(panel_id: int) -> None:
    panel = await rp_list_panel_row(panel_id)

    if panel is None or not panel["message_id"]:
        return

    channel = await get_text_channel(bot, int(panel["channel_id"]))

    if channel is None:
        return

    try:
        message = await channel.fetch_message(int(panel["message_id"]))
        await message.edit(content=await rp_list_render_text(panel_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        log.exception("Could not update RP list message %s.", panel_id)


@bot.command(name="rplist")
async def rp_list_command(ctx: commands.Context, *, body: str = "") -> None:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if not is_staff(ctx.author):
        await ctx.reply("❌ Only staff can create an RP list.", mention_author=False)
        return

    text_lines = body.split("\n")

    # Walk every line, collecting (line_index, user_id) for each new mention.
    seen: set[int] = set()
    found: list[tuple[int, int]] = []
    for index, line in enumerate(text_lines):
        for match in RP_LIST_MENTION_RE.finditer(line):
            uid = int(match.group(1))
            if uid in seen:
                continue
            seen.add(uid)
            found.append((index, uid))

    if not found:
        await ctx.reply(
            "❌ Mention the winners in the message.",
            mention_author=False,
        )
        return

    if len(found) > RP_LIST_MAX_MEMBERS:
        await ctx.reply(
            f"❌ An RP list can only hold up to **{RP_LIST_MAX_MEMBERS} members**. "
            f"You mentioned **{len(found)}**.",
            mention_author=False,
        )
        return

    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass

    panel_id = await db.execute(
        """
        INSERT INTO rp_list_panels(guild_id, channel_id, message_id, template, created_by, created_at)
        VALUES (?, ?, NULL, ?, ?, ?)
        """,
        (ctx.guild.id, ctx.channel.id, body, ctx.author.id, now_local().isoformat()),
    )

    for line_index, user_id in found:
        await db.execute(
            """
            INSERT INTO rp_list_entries(panel_id, line_index, user_id, paid)
            VALUES (?, ?, ?, 0)
            """,
            (panel_id, line_index, user_id),
        )

    message = await ctx.send(
        body,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )

    await db.execute(
        "UPDATE rp_list_panels SET message_id = ? WHERE id = ?",
        (message.id, panel_id),
    )


# Words that can be written when replying to an RP list message, e.g.
#   "@member paid"   "@member unpaid"   "@member add"   "@member remove"
# "unpaid" is listed before "paid" and the lookarounds stop "paid" from
# matching inside "unpaid".
RP_LIST_ACTION_RE = re.compile(
    r"(?<![a-z])(unpaid|paid|add|remove)(?![a-z])",
    re.IGNORECASE,
)
RP_LIST_MAX_LENGTH = 2000  # Discord message limit


def rp_list_template_add(template: str, user_id: int) -> tuple[str, int]:
    """Append a new line holding the member's mention.

    Returns (new_template, line_index_of_the_new_line)."""
    lines = str(template).rstrip("\n").split("\n")
    new_index = len(lines)
    lines.append(f"<@{user_id}>")
    return "\n".join(lines), new_index


def rp_list_template_remove(
    template: str,
    user_id: int,
    line_index: int,
) -> tuple[str, bool]:
    """Remove the member's mention from the template.

    If the line holds nobody else the whole line is deleted (and the caller
    must shift later line indexes down by one). If other members share the
    line, only this member's mention is removed.
    Returns (new_template, whole_line_deleted)."""
    lines = str(template).split("\n")
    if not 0 <= line_index < len(lines):
        return str(template), False

    stripped = re.sub(rf"<@!?{user_id}>", "", lines[line_index])

    if RP_LIST_MENTION_RE.search(stripped) is None:
        del lines[line_index]
        return "\n".join(lines), True

    lines[line_index] = stripped.rstrip()
    return "\n".join(lines), False


async def _rp_list_notice(message: discord.Message, text: str) -> None:
    """Short self-deleting note so staff know why nothing changed."""
    try:
        await message.channel.send(
            text,
            delete_after=6,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


async def process_rp_list_reply(message: discord.Message) -> bool:
    """Return True if this message was handled as an RP-list action.

    Reply to the bot's RP list message with one of:
      @member paid     -> mark paid (adds the ✅)
      @member unpaid   -> undo a paid mark (removes the ✅)
      @member add      -> add a member who was forgotten
      @member remove   -> remove a member from the list
    """
    if message.guild is None or message.author.bot:
        return False

    if not isinstance(message.author, discord.Member) or not is_high_command(message.author):
        return False

    if message.reference is None or message.reference.message_id is None:
        return False

    panel = await db.fetchone(
        "SELECT * FROM rp_list_panels WHERE message_id = ?",
        (message.reference.message_id,),
    )
    if panel is None:
        return False

    mention_match = RP_LIST_MENTION_RE.search(message.content)
    if mention_match is None:
        return False

    # Look for the action word outside of the mention itself.
    remainder = RP_LIST_MENTION_RE.sub(" ", message.content)
    action_match = RP_LIST_ACTION_RE.search(remainder)
    if action_match is None:
        return False

    action = action_match.group(1).lower()
    target_id = int(mention_match.group(1))
    panel_id = int(panel["id"])
    mention = f"<@{target_id}>"

    entry = await db.fetchone(
        "SELECT * FROM rp_list_entries WHERE panel_id = ? AND user_id = ?",
        (panel_id, target_id),
    )

    if action in ("paid", "unpaid"):
        if entry is None:
            await _rp_list_notice(message, f"❌ {mention} is not in this list. Reply `{mention} add` first.")
        else:
            await db.execute(
                "UPDATE rp_list_entries SET paid = ? WHERE panel_id = ? AND user_id = ?",
                (1 if action == "paid" else 0, panel_id, target_id),
            )
            await update_rp_list_message(panel_id)

    elif action == "add":
        if entry is not None:
            await _rp_list_notice(message, f"ℹ️ {mention} is already in this list.")
        else:
            current = await rp_list_entries(panel_id)
            new_template, new_index = rp_list_template_add(panel["template"], target_id)

            if len(current) >= RP_LIST_MAX_MEMBERS:
                await _rp_list_notice(
                    message,
                    f"❌ An RP list can only hold up to **{RP_LIST_MAX_MEMBERS} members**.",
                )
            elif len(new_template) > RP_LIST_MAX_LENGTH:
                await _rp_list_notice(message, "❌ The list is too long to add another member.")
            else:
                await db.execute(
                    "UPDATE rp_list_panels SET template = ? WHERE id = ?",
                    (new_template, panel_id),
                )
                await db.execute(
                    "INSERT INTO rp_list_entries(panel_id, line_index, user_id, paid) VALUES (?, ?, ?, 0)",
                    (panel_id, new_index, target_id),
                )
                await update_rp_list_message(panel_id)

    elif action == "remove":
        if entry is None:
            await _rp_list_notice(message, f"❌ {mention} is not in this list.")
        else:
            new_template, line_deleted = rp_list_template_remove(
                panel["template"],
                target_id,
                int(entry["line_index"]),
            )

            if not new_template.strip():
                await _rp_list_notice(message, "❌ You can't remove the last line of the list.")
            else:
                await db.execute(
                    "UPDATE rp_list_panels SET template = ? WHERE id = ?",
                    (new_template, panel_id),
                )
                await db.execute(
                    "DELETE FROM rp_list_entries WHERE panel_id = ? AND user_id = ?",
                    (panel_id, target_id),
                )
                if line_deleted:
                    await db.execute(
                        "UPDATE rp_list_entries SET line_index = line_index - 1 "
                        "WHERE panel_id = ? AND line_index > ?",
                        (panel_id, int(entry["line_index"])),
                    )
                await update_rp_list_message(panel_id)

    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass

    return True


# VC drag state: { guild_id: { user_id: asyncio.Task } }
drag_tasks: dict[int, dict[int, asyncio.Task]] = {}


def validate_configuration() -> None:
    required = {
        "DISCORD_TOKEN": bool(TOKEN),
        "BOT_OWNER_ID": BOT_OWNER_ID > 0,
        "GUILD_ID": GUILD_ID > 0,
        "REGISTRATION_CHANNEL_ID": REGISTRATION_CHANNEL_ID > 0,
        "INFORMAL_ROSTER_ROLE_ID": INFORMAL_ROSTER_ROLE_ID > 0,
        "TURFER_ROLE_ID": TURFER_ROLE_ID > 0,
        "ROLE_REQUEST_CHANNEL_ID": ROLE_REQUEST_CHANNEL_ID > 0,
        "ROLE_REVIEW_CHANNEL_ID": ROLE_REVIEW_CHANNEL_ID > 0,
        "WARN_1_ROLE_ID": WARNING_ROLE_IDS[1] > 0,
        "WARN_2_ROLE_ID": WARNING_ROLE_IDS[2] > 0,
        "WARN_3_ROLE_ID": WARNING_ROLE_IDS[3] > 0,
    }
    for rank, role_id in RANK_ROLE_IDS.items():
        required[f"RANK_{rank}_ROLE_ID"] = role_id > 0

    missing = [name for name, valid in required.items() if not valid]
    if missing:
        raise RuntimeError(
            "Missing or invalid environment variables: " + ", ".join(missing)
        )


# Part 2 must be pasted below this line.


