# === Rank request V2 ===
# Source lines 5804-6326 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# RANK REQUEST V2 — BUTTON, FORM, AND DM PROOF
# =========================================================

RANK_PROOF_TIMEOUT_MINUTES = 60


def initialize_rank_request_v2_schema() -> None:
    """Create supporting data and migrate existing rank-request databases."""
    with db.connect() as database:
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )

        existing_columns = {
            str(row["name"])
            for row in database.execute(
                "PRAGMA table_info(role_requests)"
            ).fetchall()
        }

        if "invited_by" not in existing_columns:
            database.execute(
                "ALTER TABLE role_requests ADD COLUMN invited_by TEXT"
            )

        database.commit()


async def get_setting(key: str) -> str | None:
    row = await db.fetchone(
        "SELECT value FROM bot_settings WHERE key = ?",
        (key,),
    )
    return str(row["value"]) if row else None


async def set_setting(key: str, value: str) -> None:
    await db.execute(
        """
        INSERT INTO bot_settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


async def delete_setting(key: str) -> None:
    await db.execute(
        "DELETE FROM bot_settings WHERE key = ?",
        (key,),
    )


async def get_json_setting(key: str) -> object | None:
    raw = await get_setting(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


async def set_json_setting(key: str, value: object) -> None:
    await set_setting(key, json.dumps(value))


def parse_form_rank(value: str) -> int | None:
    normalized = re.sub(r"\s+", " ", value.strip().lower())

    if normalized.isdigit():
        rank = int(normalized)
        return rank if 1 <= rank <= 6 else None

    aliases = {
        1: {"newbie", "new member"},
        2: {"suspended"},
        3: {"associated", "associate"},
        4: {"fighter"},
        5: {"turfer"},
        6: {"trusted"},
    }

    for rank, names in aliases.items():
        if normalized in names:
            return rank

    return None


class RankRequestModal(discord.ui.Modal):
    def __init__(self, client: commands.Bot) -> None:
        super().__init__(
            title="Family Rank Request",
            timeout=300,
        )
        self.client = client

        self.ingame_name = discord.ui.TextInput(
            label="In-Game Name",
            placeholder="Example: Vikku Smurf",
            min_length=2,
            max_length=24,
            required=True,
        )

        self.ingame_id = discord.ui.TextInput(
            label="ID",
            placeholder="Example: 109862",
            min_length=1,
            max_length=12,
            required=True,
        )

        self.rank = discord.ui.TextInput(
            label="Rank",
            placeholder="Example: 6 or Trusted",
            min_length=1,
            max_length=30,
            required=True,
        )

        self.invited_by = discord.ui.TextInput(
            label="Who Invited You? (Optional)",
            placeholder="Example: Dev Manus",
            min_length=0,
            max_length=50,
            required=False,
        )

        self.add_item(self.ingame_name)
        self.add_item(self.ingame_id)
        self.add_item(self.rank)
        self.add_item(self.invited_by)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ This form must be submitted inside the server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Member information could not be found.",
                ephemeral=True,
            )
            return

        game_name = re.sub(
            r"\s+",
            " ",
            str(self.ingame_name.value).strip(),
        )
        game_id = str(self.ingame_id.value).strip()
        requested_rank = parse_form_rank(str(self.rank.value))
        invited_by = re.sub(
            r"\s+",
            " ",
            str(self.invited_by.value).strip(),
        ) or None

        if not game_id.isdigit():
            await interaction.response.send_message(
                "❌ ID must contain numbers only.",
                ephemeral=True,
            )
            return

        if requested_rank is None:
            await interaction.response.send_message(
                "❌ Invalid rank. Enter a number from **1 to 6** "
                "or a valid rank name. Man Of Hood and Captain requests are disabled.",
                ephemeral=True,
            )
            return

        existing = await db.fetchone(
            """
            SELECT id, status
            FROM role_requests
            WHERE requester_id = ?
              AND status IN ('awaiting_proof', 'pending')
            ORDER BY id DESC
            LIMIT 1
            """,
            (interaction.user.id,),
        )

        if existing:
            await interaction.response.send_message(
                "❌ You already have an active rank request. "
                "Complete or wait for that request to be reviewed.",
                ephemeral=True,
            )
            return

        request_id = await db.execute(
            """
            INSERT INTO role_requests(
                guild_id,
                requester_id,
                ingame_name,
                ingame_id,
                invited_by,
                requested_rank,
                proof_url,
                original_channel_id,
                original_message_id,
                review_channel_id,
                review_message_id,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, 'awaiting_proof', ?)
            """,
            (
                interaction.guild.id,
                interaction.user.id,
                game_name,
                game_id,
                invited_by,
                requested_rank,
                ROLE_REVIEW_CHANNEL_ID,
                now_local().isoformat(),
            ),
        )

        await interaction.response.send_message(
            "✅ Your form was saved.\n"
            "Now send **one proof screenshot** in my DM within **1 hour**.",
            ephemeral=True,
        )

        try:
            await interaction.user.send(
                "📸 **Send your proof screenshot here.**\n\n"
                "• Open game\n"
                "• Press **M**\n"
                "• Go to **Stats**\n"
                "• Share the screenshot\n\n"
                f"Request ID: **{request_id}**\n"
                f"Name: **{game_name}**\n"
                f"ID: **{game_id}**\n"
                f"Rank: **{requested_rank} | {RANK_NAMES[requested_rank]}**\n"
                f"Invited By: **{invited_by or 'Not Provided'}**\n\n"
                "Send one image within **1 hour**."
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "⚠️ I could not DM you. Enable DMs from server members, "
                "then click **Rank Request** again after this request expires.",
                ephemeral=True,
            )


class RankRequestPanelView(discord.ui.View):
    def __init__(self, client: commands.Bot) -> None:
        super().__init__(timeout=None)
        self.client = client

        button = discord.ui.Button(
            label="Rank Request",
            emoji="📤",
            style=discord.ButtonStyle.primary,
            custom_id="family-rank-request:open-form",
        )
        button.callback = self.open_form
        self.add_item(button)

    async def open_form(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.send_modal(
            RankRequestModal(self.client)
        )


def rank_request_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📋 Family Rank Request",
        description=(
            "Click **Rank Request** and fill in your details.\n\n"
            "After submitting the form, send your proof screenshot "
            "to the bot in DM within **1 hour**."
        ),
        colour=Theme.PRIMARY,
    )

    ranks = "\n".join(
        f"`{number}` — {name}"
        for number, name in RANK_NAMES.items()
        if number <= 6
    )

    embed.add_field(
        name="Available Ranks",
        value=ranks,
        inline=False,
    )
    embed.add_field(
        name="Nickname After Approval",
        value="`In-Game Name | ID`",
        inline=False,
    )
    embed.set_footer(
        text="Only one active request per member is allowed."
    )
    return embed


async def ensure_rank_request_panel(
    client: commands.Bot,
) -> None:
    channel = await get_text_channel(
        client,
        ROLE_REQUEST_CHANNEL_ID,
    )

    if channel is None:
        log.error("Rank request channel was not found.")
        return

    saved_message_id = await get_setting(
        "rank_request_panel_message_id"
    )

    if saved_message_id:
        try:
            await channel.fetch_message(int(saved_message_id))
            return
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
            ValueError,
        ):
            pass

    view = RankRequestPanelView(client)
    message = await channel.send(
        embed=rank_request_panel_embed(),
        view=view,
    )
    await set_setting(
        "rank_request_panel_message_id",
        str(message.id),
    )
    client.add_view(view, message_id=message.id)
    log.info("Created rank request application panel.")


async def submit_rank_request_for_review(
    client: commands.Bot,
    request: sqlite3.Row,
    proof_url: str,
) -> None:
    review_channel = await get_text_channel(
        client,
        ROLE_REVIEW_CHANNEL_ID,
    )

    if review_channel is None:
        raise RuntimeError("Role review channel was not found.")

    request_id = int(request["id"])

    await db.execute(
        """
        UPDATE role_requests
        SET proof_url = ?,
            status = 'pending'
        WHERE id = ?
        """,
        (proof_url, request_id),
    )

    view = RoleRequestReviewView(client, request_id)

    review_message = await review_channel.send(
        embed=await role_request_embed(request_id),
        view=view,
    )

    await db.execute(
        """
        UPDATE role_requests
        SET review_message_id = ?
        WHERE id = ?
        """,
        (review_message.id, request_id),
    )

    client.add_view(
        view,
        message_id=review_message.id,
    )


async def process_rank_proof_dm(
    client: commands.Bot,
    message: discord.Message,
) -> bool:
    """Return True when the DM was handled as part of a rank request."""
    if message.guild is not None or message.author.bot:
        return False

    request = await db.fetchone(
        """
        SELECT *
        FROM role_requests
        WHERE requester_id = ?
          AND status = 'awaiting_proof'
        ORDER BY id DESC
        LIMIT 1
        """,
        (message.author.id,),
    )

    if request is None:
        return False

    created_at = datetime.fromisoformat(
        str(request["created_at"])
    )
    expires_at = created_at + timedelta(
        minutes=RANK_PROOF_TIMEOUT_MINUTES
    )

    if now_local() > expires_at:
        await db.execute(
            """
            UPDATE role_requests
            SET status = 'expired'
            WHERE id = ?
            """,
            (int(request["id"]),),
        )
        await message.channel.send(
            "❌ Your rank request expired. "
            "Return to the server and submit the form again."
        )
        return True

    image = next(
        (
            attachment
            for attachment in message.attachments
            if valid_image_attachment(attachment)
        ),
        None,
    )

    if image is None:
        await message.channel.send(
            "❌ Please send an **image screenshot**, not text."
        )
        return True

    try:
        await submit_rank_request_for_review(
            client,
            request,
            image.url,
        )
    except Exception:
        log.exception(
            "Could not forward rank request %s to staff.",
            request["id"],
        )
        await message.channel.send(
            "❌ I could not forward your request to staff. "
            "Please contact a high-command member."
        )
        return True

    await message.channel.send(
        "✅ Your proof and form were sent to staff for review."
    )
    return True


@bot.command(name="rankpanel")
async def rank_panel_command(
    ctx: commands.Context,
) -> None:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if not is_staff(ctx.author):
        await ctx.reply(
            "❌ Only authorized staff can recreate the rank request panel.",
            mention_author=False,
        )
        return

    await set_setting(
        "rank_request_panel_message_id",
        "0",
    )
    await ensure_rank_request_panel(bot)
    await ctx.reply(
        "✅ Rank request panel checked/recreated.",
        mention_author=False,
    )


initialize_rank_request_v2_schema()



