# === POV request system ===
# Loaded into shared namespace by bot.py — do not run this file alone.
#
# HOW IT WORKS
#   Complaint panel -> 🎥 POV Request button -> form:
#       Your ID / Suspect ID / Event name / Date or time (optional) / Reason
#   -> the bot posts the request in the POV review channel with  ✅ Accept  ❌ Reject
#      (Jailer role only).
#
#   ✅ Accept
#       * bot looks for a member that owns the Suspect ID
#           (nickname like "Name | 12345" or an accepted rank/role request)
#       * FOUND      -> DM the suspect: "you have 4 hours to send the POV link"
#                       The review post shows "waiting for POV" + the deadline.
#       * NOT FOUND  -> the review post says "can't find this member in the server"
#                       with two buttons:
#                         👤 Member  -> type his username, bot continues as above
#                         🚫 Revoke  -> he is not in the server, request is closed
#
#   Suspect replies to the bot's DM with a link
#       -> the review post is edited: link added + ✅ Approve  ❌ Reject buttons
#       ✅ Approve -> the video link is sent to the member who asked, in DM
#       ❌ Reject  -> reason is sent to the member who asked
#
#   Nothing within 4 hours -> request expires, the member who asked is told.
#
# Everything is stored in the `pov_requests` table, so open requests survive a restart.

POV_ACTIVE_STATUSES = ("pending", "processing", "not_found", "awaiting_pov", "pov_submitted")
POV_BUTTON_STATUSES = ("pending", "not_found", "pov_submitted")
POV_URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
POV_MAX_OPEN_PER_MEMBER = 3

POV_STATUS_LOOK: dict[str, tuple[str, discord.Colour]] = {
    "pending": ("🟡 **Pending review**", Theme.WARNING),
    "processing": ("⏳ **Processing…**", Theme.WARNING),
    "not_found": ("🟠 **Suspect not found in the server**", Theme.WARNING),
    "awaiting_pov": ("⏳ **Waiting for the suspect's POV**", Theme.PRIMARY),
    "pov_submitted": ("🔵 **POV received — needs your review**", Theme.PRIMARY),
    "approved": ("🟢 **Approved — POV sent to the requester**", Theme.SUCCESS),
    "rejected": ("🔴 **Rejected**", Theme.DANGER),
    "revoked": ("⚫ **Revoked — suspect is not in the server**", discord.Colour.dark_grey()),
    "expired": ("⌛ **Expired — no POV was sent in time**", discord.Colour.dark_grey()),
}


# ---------------------------------------------------------
# Small helpers
# ---------------------------------------------------------

def pov_case_id(request_id: int) -> str:
    return f"POV-{int(request_id):05d}"


def pov_review_channel_id() -> int:
    return POV_REVIEW_CHANNEL_ID or COMPLAINT_REVIEW_CHANNEL_ID


async def pov_row(request_id: int) -> sqlite3.Row | None:
    return await db.fetchone("SELECT * FROM pov_requests WHERE id = ?", (request_id,))


async def pov_claim(
    request_id: int,
    from_status: str,
    to_status: str,
    **fields: object,
) -> bool:
    """Atomically move a request from one status to another.
    Returns False when somebody else already moved it (two reviewers clicking at
    the same moment, double clicks, expiry loop vs. a late DM ...)."""
    sets = ["status = ?"]
    params: list[object] = [to_status]
    for column, value in fields.items():  # column names come from this file only
        sets.append(f"{column} = ?")
        params.append(value)
    params.extend([request_id, from_status])
    changed = await db.execute_count(
        f"UPDATE pov_requests SET {', '.join(sets)} WHERE id = ? AND status = ?",
        tuple(params),
    )
    return changed == 1


async def pov_dm_user(
    client: commands.Bot,
    user_id: int,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
) -> discord.Message | None:
    """Best-effort DM. Returns the sent message, or None when DMs are closed."""
    try:
        user = client.get_user(user_id) or await client.fetch_user(user_id)
        return await user.send(content=content, embed=embed)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def pov_extract_links(text: str) -> list[str]:
    links: list[str] = []
    for raw in POV_URL_PATTERN.findall(text or ""):
        link = raw.rstrip(".,;:!?)]}\"'")
        if link and link not in links:
            links.append(link)
    return links[:3]


# ---------------------------------------------------------
# Review embed
# ---------------------------------------------------------

def pov_embed(request: sqlite3.Row) -> discord.Embed:
    status = str(request["status"])
    status_text, colour = POV_STATUS_LOOK.get(status, (status.title(), Theme.PRIMARY))
    created_at = datetime.fromisoformat(str(request["created_at"]))

    embed = discord.Embed(
        title=f"🎥 POV Request • {pov_case_id(int(request['id']))}",
        description=f"<@{int(request['requester_user_id'])}> is asking for a POV.",
        colour=colour,
        timestamp=created_at,
    )
    embed.add_field(name="Your ID (requester)", value=f"**{request['requester_game_id']}**", inline=True)
    embed.add_field(name="Suspect ID", value=f"**{request['suspect_game_id']}**", inline=True)
    embed.add_field(name="Status", value=status_text, inline=True)
    embed.add_field(name="Event", value=short_text(request["event_name"], 1000), inline=True)
    embed.add_field(
        name="Date / Time",
        value=short_text(request["event_time"], 1000) or "*Not provided*",
        inline=True,
    )
    embed.add_field(name="Reason for asking", value=short_text(request["reason"], 1000), inline=False)

    if request["suspect_user_id"]:
        embed.add_field(
            name="Suspect (member)",
            value=f"<@{int(request['suspect_user_id'])}>\n`{int(request['suspect_user_id'])}`",
            inline=True,
        )

    if status == "awaiting_pov" and request["deadline_at"]:
        deadline = datetime.fromisoformat(str(request["deadline_at"]))
        embed.add_field(
            name="Deadline",
            value=f"<t:{int(deadline.timestamp())}:R>",
            inline=True,
        )

    if request["pov_link"]:
        embed.add_field(name="🔗 POV link", value=short_text(request["pov_link"], 1000), inline=False)

    if request["note"]:
        note_title = "Reject reason" if status == "rejected" else "Note"
        embed.add_field(name=note_title, value=short_text(request["note"], 1000), inline=False)

    if request["reviewed_by"] and status in ("approved", "rejected", "revoked"):
        embed.add_field(name="Handled by", value=f"<@{int(request['reviewed_by'])}>", inline=True)

    footer = {
        "pending": "Accept to look for the suspect, or Reject.",
        "not_found": "Know who this ID is? Press Member and type his username. Not in the server? Press Revoke.",
        "pov_submitted": "Watch the link, then Approve (sent to the requester) or Reject.",
        "awaiting_pov": f"The suspect has {POV_RESPONSE_HOURS} hours to reply to the bot's DM with a link.",
    }.get(status, "Jailers • POV Review")
    embed.set_footer(text=footer)
    return embed


async def refresh_pov_message(client: commands.Bot, request_id: int) -> None:
    """Re-draw the review post for the current status. If the post was deleted it
    is posted again, so a request can never become unreachable."""
    request = await pov_row(request_id)
    if request is None:
        return

    channel = await get_text_channel(client, int(request["review_channel_id"]))
    if channel is None:
        log.error("POV review channel %s was not found.", request["review_channel_id"])
        return

    status = str(request["status"])
    view = PovReviewView(client, request_id, status) if status in POV_BUTTON_STATUSES else None
    embed = pov_embed(request)

    message: discord.Message | None = None
    if request["review_message_id"]:
        try:
            message = await channel.fetch_message(int(request["review_message_id"]))
        except discord.NotFound:
            message = None
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not fetch POV review message for %s.", request_id)
            return

    try:
        if message is None:
            if view is not None:
                message = await channel.send(embed=embed, view=view)
            else:
                message = await channel.send(embed=embed)
            await db.execute(
                "UPDATE pov_requests SET review_message_id = ? WHERE id = ?",
                (message.id, request_id),
            )
        else:
            await message.edit(embed=embed, view=view)  # view=None removes the buttons
        if view is not None:
            client.add_view(view, message_id=message.id)
    except discord.HTTPException:
        log.exception("Could not update POV review message for %s.", request_id)


# ---------------------------------------------------------
# Finding the suspect
# ---------------------------------------------------------

async def find_suspect_candidates(
    guild: discord.Guild,
    game_id: str,
) -> list[discord.Member]:
    """Members that own this in-game ID: an accepted rank/role request with the
    ID, or the ID written in the nickname (e.g. 'Name | 12345')."""
    found: dict[int, discord.Member] = {}

    rows = await db.fetchall(
        """
        SELECT DISTINCT requester_id
        FROM role_requests
        WHERE ingame_id = ? AND status = 'accepted'
        """,
        (game_id,),
    )
    for row in rows:
        member = await fetch_guild_member(guild, int(row["requester_id"]))
        if member is not None and not member.bot:
            found[member.id] = member

    pattern = re.compile(rf"(?<!\d){re.escape(game_id)}(?!\d)")
    for member in guild.members:
        if member.bot or member.id in found:
            continue
        if pattern.search(member.display_name) or pattern.search(member.name):
            found[member.id] = member

    return list(found.values())


async def resolve_member_from_text(
    guild: discord.Guild,
    text: str,
) -> list[discord.Member]:
    """Turn what a Jailer typed (username / nickname / @mention / Discord ID)
    into matching members."""
    text = text.strip().lstrip("@").strip()
    if not text:
        return []

    id_match = re.fullmatch(r"<@!?(\d{15,22})>|(\d{15,22})", text)
    if id_match:
        member = await fetch_guild_member(guild, int(id_match.group(1) or id_match.group(2)))
        return [member] if member is not None and not member.bot else []

    lowered = text.lower()

    def names(member: discord.Member) -> set[str]:
        return {
            member.name.lower(),
            member.display_name.lower(),
            (member.global_name or "").lower(),
            (member.nick or "").lower(),
        }

    exact = [m for m in guild.members if not m.bot and lowered in names(m)]
    if exact:
        return exact

    try:
        results = await guild.query_members(query=text[:100], limit=10)
    except (discord.HTTPException, asyncio.TimeoutError):
        results = []

    results = [m for m in results if not m.bot]
    exact = [m for m in results if lowered in names(m)]
    return exact or results


# ---------------------------------------------------------
# Starting the 4-hour POV window
# ---------------------------------------------------------

async def begin_pov_collection(
    client: commands.Bot,
    request: sqlite3.Row,
    member: discord.Member,
) -> bool:
    """DM the suspect and move the request processing -> awaiting_pov.
    Returns False (and changes nothing) when the suspect's DMs are closed."""
    deadline = now_local() + timedelta(hours=POV_RESPONSE_HOURS)

    dm = discord.Embed(
        title="🎥 POV requested",
        description=(
            "Staff asked you for your **POV** (video) of the event below.\n"
            f"You have **{POV_RESPONSE_HOURS} hours** to send it."
        ),
        colour=Theme.PRIMARY,
        timestamp=now_local(),
    )
    dm.add_field(name="Event", value=short_text(request["event_name"], 1000), inline=True)
    dm.add_field(
        name="Date / Time",
        value=short_text(request["event_time"], 1000) or "*Not provided*",
        inline=True,
    )
    dm.add_field(name="Deadline", value=f"<t:{int(deadline.timestamp())}:R>", inline=True)
    dm.add_field(
        name="How to send it",
        value=(
            "Reply to **this message** with the video **link** "
            "(YouTube, Medal, Streamable, Google Drive …).\n"
            "Uploaded video files can't be used — please send a link."
        ),
        inline=False,
    )
    dm.set_footer(text=f"{pov_case_id(int(request['id']))} • Reply to this DM")

    sent = await pov_dm_user(client, member.id, embed=dm)
    if sent is None:
        return False

    claimed = await pov_claim(
        int(request["id"]),
        "processing",
        "awaiting_pov",
        suspect_user_id=member.id,
        dm_message_id=sent.id,
        deadline_at=deadline.isoformat(),
        note=None,
    )
    return claimed


async def mark_not_found(client: commands.Bot, request_id: int, note: str) -> None:
    await pov_claim(request_id, "processing", "not_found", note=note)
    await refresh_pov_message(client, request_id)


async def process_accepted_request(
    client: commands.Bot,
    request_id: int,
) -> str:
    """Runs after a Jailer pressed Accept (status is 'processing').
    Returns the text to show that Jailer."""
    request = await pov_row(request_id)
    guild = client.get_guild(int(request["guild_id"])) if request else None
    if request is None or guild is None:
        await mark_not_found(client, request_id, "The server could not be reached. Press Member to try again.")
        return "⚠️ I couldn't reach the server. Use the Member button to continue."

    candidates = await find_suspect_candidates(guild, str(request["suspect_game_id"]))

    if len(candidates) == 1:
        member = candidates[0]
        if await begin_pov_collection(client, request, member):
            await refresh_pov_message(client, request_id)
            return f"✅ Found {member.mention}. I sent them a DM — they have {POV_RESPONSE_HOURS} hours."
        await mark_not_found(
            client,
            request_id,
            f"Found {member.mention} but their DMs are closed. "
            "Press Member to try another account, or Revoke.",
        )
        return f"⚠️ Found {member.mention} but their DMs are closed. Use Member or Revoke."

    if not candidates:
        note = f"No member in the server has Suspect ID **{request['suspect_game_id']}**."
    else:
        listed = ", ".join(f"{m.mention}" for m in candidates[:5])
        note = f"Several members match ID **{request['suspect_game_id']}**: {listed}. Press Member and type the right one."
    await mark_not_found(client, request_id, note)
    return "⚠️ I can't find this member in the server. Use **Member** (type his username) or **Revoke**."


# ---------------------------------------------------------
# Request form
# ---------------------------------------------------------

class PovRequestModal(discord.ui.Modal):
    def __init__(self, client: commands.Bot) -> None:
        super().__init__(title="POV Request", timeout=600)
        self.client = client

        self.your_id = discord.ui.TextInput(
            label="Your ID",
            placeholder="Example: 243859",
            min_length=1,
            max_length=12,
            required=True,
        )
        self.suspect_id = discord.ui.TextInput(
            label="Suspect ID",
            placeholder="Example: 123456",
            min_length=1,
            max_length=12,
            required=True,
        )
        self.event_name = discord.ui.TextInput(
            label="Event name",
            placeholder="Which event do you want the POV of?",
            min_length=2,
            max_length=100,
            required=True,
        )
        self.event_time = discord.ui.TextInput(
            label="Date or time (optional)",
            placeholder="Example: 5 Nov, around 9 PM",
            max_length=100,
            required=False,
        )
        self.reason = discord.ui.TextInput(
            label="Reason for asking POV",
            placeholder="Why do you need this POV?",
            style=discord.TextStyle.paragraph,
            min_length=5,
            max_length=800,
            required=True,
        )
        for item in (self.your_id, self.suspect_id, self.event_name, self.event_time, self.reason):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Use this form inside the server.", ephemeral=True)
            return

        your_id = str(self.your_id.value).strip()
        suspect_id = str(self.suspect_id.value).strip()
        event_name = str(self.event_name.value).strip()
        event_time = str(self.event_time.value).strip()
        reason = str(self.reason.value).strip()

        if not your_id.isdigit() or not suspect_id.isdigit():
            await interaction.response.send_message("❌ Both IDs must contain numbers only.", ephemeral=True)
            return

        if your_id == suspect_id:
            await interaction.response.send_message(
                "❌ Your ID and the suspect ID are the same. Check the IDs and try again.",
                ephemeral=True,
            )
            return

        channel = await get_text_channel(self.client, pov_review_channel_id())
        if channel is None:
            await interaction.response.send_message(
                "❌ The POV review channel is not set up. Tell a Jailer.",
                ephemeral=True,
            )
            return

        placeholders = ",".join("?" for _ in POV_ACTIVE_STATUSES)
        open_rows = await db.fetchall(
            f"""
            SELECT suspect_game_id, event_name FROM pov_requests
            WHERE requester_user_id = ? AND status IN ({placeholders})
            """,
            (interaction.user.id, *POV_ACTIVE_STATUSES),
        )
        if any(
            str(row["suspect_game_id"]) == suspect_id
            and str(row["event_name"]).strip().lower() == event_name.lower()
            for row in open_rows
        ):
            await interaction.response.send_message(
                "⚠️ You already have an open POV request for this suspect and event.",
                ephemeral=True,
            )
            return
        if len(open_rows) >= POV_MAX_OPEN_PER_MEMBER:
            await interaction.response.send_message(
                f"⚠️ You already have {POV_MAX_OPEN_PER_MEMBER} open POV requests. "
                "Wait until staff finish one.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        request_id = await db.execute(
            """
            INSERT INTO pov_requests(
                guild_id, requester_user_id, requester_game_id, suspect_game_id,
                event_name, event_time, reason, review_channel_id, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                interaction.guild.id,
                interaction.user.id,
                your_id,
                suspect_id,
                event_name,
                event_time or None,
                reason,
                channel.id,
                now_local().isoformat(),
            ),
        )

        request = await pov_row(request_id)
        view = PovReviewView(self.client, request_id, "pending")
        try:
            message = await channel.send(embed=pov_embed(request), view=view)
        except discord.HTTPException:
            log.exception("Could not post POV request %s.", request_id)
            await db.execute("DELETE FROM pov_requests WHERE id = ?", (request_id,))
            await interaction.followup.send(
                "❌ I couldn't send your request to staff (missing channel permission). Tell a Jailer.",
                ephemeral=True,
            )
            return

        await db.execute(
            "UPDATE pov_requests SET review_message_id = ? WHERE id = ?",
            (message.id, request_id),
        )
        self.client.add_view(view, message_id=message.id)

        await interaction.followup.send(
            f"✅ Your POV request **{pov_case_id(request_id)}** was sent to staff. "
            "You'll get a DM when it's decided.",
            ephemeral=True,
        )


# ---------------------------------------------------------
# Review buttons (Jailer only)
# ---------------------------------------------------------

class PovRejectModal(discord.ui.Modal):
    def __init__(self, client: commands.Bot, request_id: int, from_status: str) -> None:
        super().__init__(title="Reject POV Request", timeout=300)
        self.client = client
        self.request_id = request_id
        self.from_status = from_status
        self.reason = discord.ui.TextInput(
            label="Reason (sent to the requester)",
            style=discord.TextStyle.paragraph,
            min_length=3,
            max_length=500,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await jailer_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        reason = str(self.reason.value).strip()
        claimed = await pov_claim(
            self.request_id,
            self.from_status,
            "rejected",
            reviewed_by=interaction.user.id,
            reviewed_at=now_local().isoformat(),
            note=reason,
        )
        if not claimed:
            await interaction.followup.send("❌ This request was already handled.", ephemeral=True)
            return

        request = await pov_row(self.request_id)
        await refresh_pov_message(self.client, self.request_id)

        dm_ok = await pov_dm_user(
            self.client,
            int(request["requester_user_id"]),
            embed=discord.Embed(
                title="❌ POV request rejected",
                description=(
                    f"**Case:** {pov_case_id(self.request_id)}\n"
                    f"**Event:** {short_text(request['event_name'], 200)}\n\n"
                    f"**Reason:** {reason}"
                ),
                colour=Theme.DANGER,
            ),
        )
        await interaction.followup.send(
            "❌ Rejected. The requester was notified."
            if dm_ok
            else "❌ Rejected. (The requester's DMs are closed, so they were not messaged.)",
            ephemeral=True,
        )


class PovMemberModal(discord.ui.Modal):
    def __init__(self, client: commands.Bot, request_id: int) -> None:
        super().__init__(title="Who is the suspect?", timeout=300)
        self.client = client
        self.request_id = request_id
        self.username = discord.ui.TextInput(
            label="Member username",
            placeholder="Username, nickname or Discord ID",
            min_length=2,
            max_length=100,
            required=True,
        )
        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await jailer_only(interaction):
            return
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)

        matches = await resolve_member_from_text(interaction.guild, str(self.username.value))
        if not matches:
            await interaction.followup.send(
                "❌ No member with that username in this server. Try again, or press **Revoke**.",
                ephemeral=True,
            )
            return
        if len(matches) > 1:
            listed = "\n".join(f"• {m.mention} — `{m.name}` (`{m.id}`)" for m in matches[:8])
            await interaction.followup.send(
                f"⚠️ More than one member matches. Type the exact username or Discord ID:\n{listed}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        member = matches[0]
        claimed = await pov_claim(
            self.request_id,
            "not_found",
            "processing",
            reviewed_by=interaction.user.id,
            reviewed_at=now_local().isoformat(),
        )
        if not claimed:
            await interaction.followup.send("❌ This request was already handled.", ephemeral=True)
            return

        request = await pov_row(self.request_id)
        try:
            ok = await begin_pov_collection(self.client, request, member)
        except Exception:
            log.exception("POV collection failed for %s.", self.request_id)
            ok = False

        if ok:
            await refresh_pov_message(self.client, self.request_id)
            await interaction.followup.send(
                f"✅ I sent {member.mention} a DM. They have {POV_RESPONSE_HOURS} hours.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await mark_not_found(
                self.client,
                self.request_id,
                f"{member.mention}'s DMs are closed, so I could not ask for the POV. "
                "Press Member to try another account, or Revoke.",
            )
            await interaction.followup.send(
                f"⚠️ I couldn't DM {member.mention} (DMs closed).",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )


class PovReviewView(discord.ui.View):
    """Persistent. Which buttons exist depends on the request status, so the
    same class restores correctly after a restart."""

    def __init__(self, client: commands.Bot, request_id: int, status: str) -> None:
        super().__init__(timeout=None)
        self.client = client
        self.request_id = request_id

        def add(label: str, emoji: str, style: discord.ButtonStyle, key: str, callback) -> None:
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"pov:{key}:{request_id}",
            )
            button.callback = callback
            self.add_item(button)

        if status == "pending":
            add("Accept", "✅", discord.ButtonStyle.success, "accept", self.accept_callback)
            add("Reject", "❌", discord.ButtonStyle.danger, "reject", self.reject_pending_callback)
        elif status == "not_found":
            add("Member", "👤", discord.ButtonStyle.primary, "member", self.member_callback)
            add("Revoke", "🚫", discord.ButtonStyle.danger, "revoke", self.revoke_callback)
        elif status == "pov_submitted":
            add("Approve", "✅", discord.ButtonStyle.success, "approve", self.approve_callback)
            add("Reject", "❌", discord.ButtonStyle.danger, "rejectpov", self.reject_pov_callback)

    # ---- pending: Accept / Reject
    async def accept_callback(self, interaction: discord.Interaction) -> None:
        if not await jailer_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        claimed = await pov_claim(
            self.request_id,
            "pending",
            "processing",
            reviewed_by=interaction.user.id,
            reviewed_at=now_local().isoformat(),
        )
        if not claimed:
            await respond_or_followup(interaction, "❌ This request was already handled.")
            return

        try:
            text = await process_accepted_request(self.client, self.request_id)
        except Exception:
            log.exception("Accepting POV request %s failed.", self.request_id)
            # Never leave it stuck in 'processing': put it back so it can be retried.
            await pov_claim(self.request_id, "processing", "pending")
            await refresh_pov_message(self.client, self.request_id)
            await respond_or_followup(interaction, "❌ Something went wrong. Press Accept again.")
            return

        await respond_or_followup(interaction, text)

    async def reject_pending_callback(self, interaction: discord.Interaction) -> None:
        if not await jailer_only(interaction):
            return
        request = await pov_row(self.request_id)
        if request is None or str(request["status"]) != "pending":
            await interaction.response.send_message("❌ This request was already handled.", ephemeral=True)
            return
        await interaction.response.send_modal(PovRejectModal(self.client, self.request_id, "pending"))

    # ---- not found: Member / Revoke
    async def member_callback(self, interaction: discord.Interaction) -> None:
        if not await jailer_only(interaction):
            return
        request = await pov_row(self.request_id)
        if request is None or str(request["status"]) != "not_found":
            await interaction.response.send_message("❌ This request was already handled.", ephemeral=True)
            return
        await interaction.response.send_modal(PovMemberModal(self.client, self.request_id))

    async def revoke_callback(self, interaction: discord.Interaction) -> None:
        if not await jailer_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        claimed = await pov_claim(
            self.request_id,
            "not_found",
            "revoked",
            reviewed_by=interaction.user.id,
            reviewed_at=now_local().isoformat(),
            note="This ID does not belong to anyone in the server.",
        )
        if not claimed:
            await respond_or_followup(interaction, "❌ This request was already handled.")
            return

        request = await pov_row(self.request_id)
        await refresh_pov_message(self.client, self.request_id)
        await pov_dm_user(
            self.client,
            int(request["requester_user_id"]),
            embed=discord.Embed(
                title="🚫 POV request closed",
                description=(
                    f"**Case:** {pov_case_id(self.request_id)}\n"
                    f"**Event:** {short_text(request['event_name'], 200)}\n\n"
                    f"Suspect ID **{request['suspect_game_id']}** is not a member of this server, "
                    "so no POV can be requested. Double-check the ID if you think this is a mistake."
                ),
                colour=Theme.DANGER,
            ),
        )
        await respond_or_followup(interaction, "🚫 Request revoked. The requester was notified.")

    # ---- POV received: Approve / Reject
    async def approve_callback(self, interaction: discord.Interaction) -> None:
        if not await jailer_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        claimed = await pov_claim(
            self.request_id,
            "pov_submitted",
            "approved",
            reviewed_by=interaction.user.id,
            reviewed_at=now_local().isoformat(),
        )
        if not claimed:
            await respond_or_followup(interaction, "❌ This request was already handled.")
            return

        request = await pov_row(self.request_id)
        embed = discord.Embed(
            title="🎥 Your POV is ready",
            description=(
                f"**Case:** {pov_case_id(self.request_id)}\n"
                f"**Event:** {short_text(request['event_name'], 200)}"
                + (f"\n**Date / Time:** {short_text(request['event_time'], 200)}" if request["event_time"] else "")
            ),
            colour=Theme.SUCCESS,
        )
        embed.add_field(name="🔗 POV link", value=short_text(request["pov_link"], 1000), inline=False)

        delivered = await pov_dm_user(self.client, int(request["requester_user_id"]), embed=embed)
        if delivered is None:
            await pov_claim(
                self.request_id,
                "approved",
                "approved",
                note="Approved, but the requester's DMs are closed — give them the link manually.",
            )
        await refresh_pov_message(self.client, self.request_id)

        await respond_or_followup(
            interaction,
            "✅ Approved. The video link was sent to the requester."
            if delivered
            else "✅ Approved, but the requester's DMs are closed — send them the link yourself.",
        )

    async def reject_pov_callback(self, interaction: discord.Interaction) -> None:
        if not await jailer_only(interaction):
            return
        request = await pov_row(self.request_id)
        if request is None or str(request["status"]) != "pov_submitted":
            await interaction.response.send_message("❌ This request was already handled.", ephemeral=True)
            return
        await interaction.response.send_modal(PovRejectModal(self.client, self.request_id, "pov_submitted"))


# ---------------------------------------------------------
# Suspect's DM reply (called from on_message in data_setup.py)
# ---------------------------------------------------------

async def process_pov_dm(client: commands.Bot, message: discord.Message) -> bool:
    """Return True when this DM was handled as a POV answer."""
    if message.guild is not None or message.author.bot:
        return False

    rows = await db.fetchall(
        """
        SELECT * FROM pov_requests
        WHERE suspect_user_id = ? AND status = 'awaiting_pov'
        ORDER BY id
        """,
        (message.author.id,),
    )
    if not rows:
        return False

    content = message.content or ""
    if content.startswith("!"):
        return False  # let commands through

    links = pov_extract_links(content)
    if not links:
        await message.channel.send(
            "📎 Please send the POV as a **link** (YouTube, Medal, Streamable, Google Drive …). "
            "Uploaded video files can't be used."
        )
        return True

    now = now_local()
    live = [
        row for row in rows
        if row["deadline_at"] and datetime.fromisoformat(str(row["deadline_at"])) > now
    ]
    if not live:
        await message.channel.send(
            f"⌛ Sorry, the {POV_RESPONSE_HOURS}-hour time to send this POV is over."
        )
        return True

    target = None
    reference = message.reference.message_id if message.reference else None
    if reference:
        target = next((row for row in live if row["dm_message_id"] == reference), None)
    if target is None:
        if len(live) == 1:
            target = live[0]
        else:
            await message.channel.send(
                "You have **more than one** POV request open. Please use **Reply** on the "
                "request message you are answering, then send the link."
            )
            return True

    request_id = int(target["id"])
    claimed = await pov_claim(
        request_id,
        "awaiting_pov",
        "pov_submitted",
        pov_link="\n".join(links),
    )
    if not claimed:
        await message.channel.send("⚠️ This POV request is no longer open.")
        return True

    await refresh_pov_message(client, request_id)
    await message.channel.send("✅ Thank you! Your POV was received and sent to staff for review.")
    return True


# ---------------------------------------------------------
# Expiry + restart restore
# ---------------------------------------------------------

async def expire_pov_requests(client: commands.Bot) -> None:
    rows = await db.fetchall(
        "SELECT * FROM pov_requests WHERE status = 'awaiting_pov' AND deadline_at <= ?",
        (now_local().isoformat(),),
    )
    for request in rows:
        request_id = int(request["id"])
        claimed = await pov_claim(
            request_id,
            "awaiting_pov",
            "expired",
            reviewed_at=now_local().isoformat(),
            note=f"The suspect did not send a POV within {POV_RESPONSE_HOURS} hours.",
        )
        if not claimed:
            continue

        await refresh_pov_message(client, request_id)
        await pov_dm_user(
            client,
            int(request["requester_user_id"]),
            embed=discord.Embed(
                title="⌛ POV request expired",
                description=(
                    f"**Case:** {pov_case_id(request_id)}\n"
                    f"**Event:** {short_text(request['event_name'], 200)}\n\n"
                    f"The suspect did not send a POV within {POV_RESPONSE_HOURS} hours."
                ),
                colour=discord.Colour.dark_grey(),
            ),
        )
        if request["suspect_user_id"]:
            await pov_dm_user(
                client,
                int(request["suspect_user_id"]),
                content=f"⌛ The time to send the POV for **{short_text(request['event_name'], 100)}** has ended.",
            )


@tasks.loop(minutes=5)
async def pov_expiry_scheduler() -> None:
    try:
        await expire_pov_requests(bot)
    except Exception:
        # A failure in one pass must never kill the loop.
        log.exception("POV expiry pass failed.")


@pov_expiry_scheduler.before_loop
async def before_pov_expiry_scheduler() -> None:
    await bot.wait_until_ready()


async def restore_pov_views(client: commands.Bot) -> None:
    rows = await db.fetchall(
        """
        SELECT id, review_message_id, status
        FROM pov_requests
        WHERE review_message_id IS NOT NULL
          AND status IN ('pending', 'not_found', 'pov_submitted')
        """
    )
    for row in rows:
        client.add_view(
            PovReviewView(client, int(row["id"]), str(row["status"])),
            message_id=int(row["review_message_id"]),
        )

    # A request stuck in 'processing' means the bot restarted mid-accept:
    # put it back so a Jailer can press Accept again.
    stuck = await db.fetchall("SELECT id FROM pov_requests WHERE status = 'processing'")
    if stuck:
        await db.execute("UPDATE pov_requests SET status = 'pending' WHERE status = 'processing'")
        for row in stuck:
            await refresh_pov_message(client, int(row["id"]))
