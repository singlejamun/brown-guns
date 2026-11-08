# === Security lockdown + anti-hack detection ===
# Source lines 12324-12519 from original bot.py + new hack detection
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# SECURITY LOCKDOWN — !security / !securityoff
# =========================================================

# Every permission we touch on @everyone during a lockdown, so we can
# snapshot the previous value and restore it exactly on !securityoff.
SECURITY_LOCK_PERMISSIONS = (
    "send_messages",
    "send_messages_in_threads",
    "create_public_threads",
    "create_private_threads",
    "add_reactions",
    "speak",
    "request_to_speak",
)


def _security_state_key(guild_id: int) -> str:
    return f"security_lockdown:{guild_id}"


def _owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not (BOT_OWNER_ID > 0 and ctx.author.id == BOT_OWNER_ID):
            await ctx.reply(
                "❌ This command is restricted to the bot owner.",
                mention_author=False,
            )
            return False
        return True

    return commands.check(predicate)


LOCKABLE_CHANNEL_TYPES = (
    discord.TextChannel,
    discord.VoiceChannel,
    discord.StageChannel,
    discord.ForumChannel,
)


@bot.command(name="security")
@_owner_only()
@commands.guild_only()
async def security_lock_command(ctx: commands.Context) -> None:
    """Lock every channel in this server so normal members can't post."""
    guild = ctx.guild
    if guild is None:
        return

    state_key = _security_state_key(guild.id)
    existing = await get_json_setting(state_key)

    if existing:
        await ctx.reply(
            "🔒 This server is already locked down. "
            "Use `!securityoff` to unlock it first.",
            mention_author=False,
        )
        return

    everyone = guild.default_role
    status_message = await ctx.reply(
        "🔒 Locking down every channel, please wait...",
        mention_author=False,
    )

    snapshot: list[dict] = []
    locked = 0
    failed = 0

    for channel in guild.channels:
        if not isinstance(channel, LOCKABLE_CHANNEL_TYPES):
            continue

        overwrite = channel.overwrites_for(everyone)
        previous = {
            perm: getattr(overwrite, perm)
            for perm in SECURITY_LOCK_PERMISSIONS
        }

        for perm in SECURITY_LOCK_PERMISSIONS:
            setattr(overwrite, perm, False)

        try:
            await channel.set_permissions(
                everyone,
                overwrite=overwrite,
                reason=f"Server lockdown by {ctx.author} ({ctx.author.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
            continue

        snapshot.append({"channel_id": channel.id, "previous": previous})
        locked += 1

    await set_json_setting(state_key, snapshot)

    result_text = f"🔒 **Server locked down.** {locked} channel(s) locked"
    if failed:
        result_text += f", {failed} channel(s) failed (missing permissions)"
    result_text += ". No one can send messages until `!securityoff` is used."

    await status_message.edit(content=result_text)

    if isinstance(ctx.author, discord.Member):
        await log_staff_action(
            action="Security Lockdown Enabled",
            controller=ctx.author,
            target=guild.name,
            result=f"{locked} channel(s) locked, {failed} failed.",
        )


@bot.command(name="securityoff")
@_owner_only()
@commands.guild_only()
async def security_unlock_command(ctx: commands.Context) -> None:
    """Undo a !security lockdown and restore previous channel permissions."""
    guild = ctx.guild
    if guild is None:
        return

    state_key = _security_state_key(guild.id)
    snapshot = await get_json_setting(state_key)

    if not snapshot:
        await ctx.reply(
            "🔓 This server isn't currently locked down.",
            mention_author=False,
        )
        return

    everyone = guild.default_role
    status_message = await ctx.reply(
        "🔓 Unlocking every channel, please wait...",
        mention_author=False,
    )

    restored = 0
    failed = 0
    missing = 0

    for entry in snapshot:
        channel = guild.get_channel(int(entry["channel_id"]))
        if channel is None:
            missing += 1
            continue

        overwrite = channel.overwrites_for(everyone)
        previous = entry.get("previous", {})

        for perm in SECURITY_LOCK_PERMISSIONS:
            setattr(overwrite, perm, previous.get(perm))

        try:
            if overwrite.is_empty():
                await channel.set_permissions(
                    everyone,
                    overwrite=None,
                    reason=f"Server unlock by {ctx.author} ({ctx.author.id})",
                )
            else:
                await channel.set_permissions(
                    everyone,
                    overwrite=overwrite,
                    reason=f"Server unlock by {ctx.author} ({ctx.author.id})",
                )
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
            continue

        restored += 1

    await delete_setting(state_key)

    result_text = f"🔓 **Server unlocked.** {restored} channel(s) restored"
    if failed:
        result_text += f", {failed} channel(s) failed"
    if missing:
        result_text += f", {missing} channel(s) no longer exist"
    result_text += "."

    await status_message.edit(content=result_text)

    if isinstance(ctx.author, discord.Member):
        await log_staff_action(
            action="Security Lockdown Disabled",
            controller=ctx.author,
            target=guild.name,
            result=f"{restored} channel(s) restored, {failed} failed, {missing} missing.",
        )


# =========================================================
# ANTI-HACK / COMPROMISED ACCOUNT DETECTION
# Detects MrBeast-style phishing / scam messages from hacked accounts.
# Patterns: .txt files + OCR text extracted from images in security_patterns/
# Runtime: matches message text/embeds, image captions, and OCR of attachments.
# =========================================================

_SECURITY_PATTERNS: list[str] = []
_SECURITY_PATTERNS_LOADED_AT: float | None = None

# Built-in high-confidence patterns (always active even if folder is empty)
_BUILTIN_HACK_PATTERNS = [
    "mrbeast is giving away",
    "mr beast giveaway",
    "mrbeast giveaway",
    "free nitro from mrbeast",
    "steam gift from mrbeast",
    "you have been selected by mrbeast",
    "claim your free nitro",
    "discord.gift/",
    "free discord nitro",
    "nitro gift for you",
    "free nitro gift",
    "steamcommunity.com/gift",
    "discordapp.com/gifts/",
    "verify to claim your prize",
    "click here to claim",
    "congratulation you won",
    "you won a gift",
]

# Image extensions we will OCR / treat as screenshots
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "image/bmp",
}

# Max bytes to download for OCR (2 MB)
_OCR_MAX_BYTES = 2 * 1024 * 1024


def _security_patterns_dir() -> Path:
    # When files are exec()'d, __file__ is bot.py. Patterns live next to bot.py.
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parent / "security_patterns")
    except Exception:
        pass
    candidates.append(Path.cwd() / "security_patterns")
    try:
        candidates.append(DATABASE_PATH.resolve().parent / "security_patterns")
    except Exception:
        pass
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0] if candidates else Path("security_patterns")


_TESSERACT_AVAILABLE: bool | None = None


def _tesseract_available() -> bool:
    """Check ONCE whether the Tesseract *binary* exists (pytesseract is only a
    wrapper). Without this check every screenshot posted in the server produced
    a full traceback in the logs, and OCR-based scam detection silently did
    nothing."""
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is None:
        try:
            import pytesseract

            version = pytesseract.get_tesseract_version()
            _TESSERACT_AVAILABLE = True
            log.info("Tesseract OCR available (version %s).", version)
        except Exception:
            _TESSERACT_AVAILABLE = False
            log.warning(
                "Tesseract binary NOT found — screenshot (OCR) hack detection and "
                "image patterns in security_patterns/ are DISABLED. Text-based "
                "detection still works. On Railway, deploy with the included "
                "nixpacks.toml (installs tesseract), or apt install tesseract-ocr."
            )
    return _TESSERACT_AVAILABLE


def _ocr_image_bytes(data: bytes) -> str:
    """Run Tesseract OCR on raw image bytes. Returns extracted text (may be empty)."""
    try:
        import io
        from PIL import Image
        import pytesseract
    except ImportError:
        log.warning(
            "OCR dependencies missing (Pillow / pytesseract). "
            "Install: pip install Pillow pytesseract  + system package tesseract-ocr"
        )
        return ""

    if not _tesseract_available():
        return ""

    try:
        # Some pattern images carry damaged EXIF metadata; Pillow prints
        # "Truncated File Read" for it. It is harmless (OCR only needs the pixels),
        # so hide that warning instead of filling the logs.
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*Truncated File Read.*")
            img = Image.open(io.BytesIO(data))
            # Convert to RGB so unusual modes don't break tesseract
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            text = pytesseract.image_to_string(img) or ""
        return text.strip()
    except Exception:
        log.exception("OCR failed on image bytes (%s bytes)", len(data))
        return ""


def _ocr_image_path(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        log.warning("Could not read image for OCR: %s", path)
        return ""
    if len(data) > _OCR_MAX_BYTES:
        log.warning("Skipping OCR on large pattern image %s (%s bytes)", path, len(data))
        return ""
    return _ocr_image_bytes(data)


def load_security_patterns(force: bool = False) -> int:
    """Load detection patterns from security_patterns/ (.txt + OCR of images).
    Returns the total number of patterns loaded."""
    global _SECURITY_PATTERNS, _SECURITY_PATTERNS_LOADED_AT

    patterns: set[str] = set()
    for p in _BUILTIN_HACK_PATTERNS:
        cleaned = p.strip().lower()
        if cleaned:
            patterns.add(cleaned)

    folder = _security_patterns_dir()
    ocr_count = 0
    if folder.is_dir():
        # Text patterns
        for path in sorted(folder.glob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                log.warning("Could not read security pattern file: %s", path)
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.add(line.lower())

        # Image patterns — OCR screenshots dropped into the folder
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _IMAGE_EXTENSIONS:
                continue
            extracted = _ocr_image_path(path)
            if not extracted:
                continue
            ocr_count += 1
            # Add full block + each non-trivial line as a pattern
            for line in extracted.splitlines():
                line = line.strip()
                if len(line) >= 6:
                    patterns.add(line.lower())
            # Also keep a compressed version of the whole OCR text
            compact = " ".join(extracted.split())
            if len(compact) >= 10:
                patterns.add(compact[:200].lower())

    _SECURITY_PATTERNS = sorted(patterns, key=len, reverse=True)
    _SECURITY_PATTERNS_LOADED_AT = time.time()
    log.info(
        "Loaded %d security (hack) detection patterns from %s (OCR'd %d image(s)).",
        len(_SECURITY_PATTERNS),
        folder,
        ocr_count,
    )
    return len(_SECURITY_PATTERNS)


def is_hack_message(content: str, *, allow_short: bool = False) -> str | None:
    """Return the matched pattern string if the message looks like a
    compromised-account / MrBeast-style scam post, else None.
    allow_short=True skips the minimum-word filter (used for OCR snippets)."""
    if not content or not _SECURITY_PATTERNS:
        return None
    lower = content.lower()
    if not allow_short:
        if len(lower.split()) < 3 and "http" not in lower and "discord.gift" not in lower:
            return None
    for pattern in _SECURITY_PATTERNS:
        if pattern in lower:
            return pattern
    return None


def _attachment_is_image(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.lower() in _IMAGE_CONTENT_TYPES:
        return True
    name = (att.filename or "").lower()
    return any(name.endswith(ext) for ext in _IMAGE_EXTENSIONS)


async def _ocr_attachment(att: discord.Attachment) -> str:
    """Download an image attachment and OCR it. Returns extracted text."""
    if not _tesseract_available():
        return ""
    if att.size and att.size > _OCR_MAX_BYTES:
        log.info("Skipping OCR on large attachment %s (%s bytes)", att.filename, att.size)
        return ""
    try:
        data = await att.read()
    except (discord.HTTPException, discord.NotFound, discord.Forbidden):
        log.warning("Could not download attachment %s for OCR", att.filename)
        return ""
    if not data:
        return ""
    # Run blocking OCR off the event loop
    return await asyncio.to_thread(_ocr_image_bytes, data)


async def detect_hack_in_message(message: discord.Message) -> tuple[str | None, str]:
    """Inspect message text, embeds, captions, and image attachments (OCR).
    Returns (matched_pattern or None, source_description)."""
    # 1) Plain message content
    matched = is_hack_message(message.content or "")
    if matched:
        return matched, "message text"

    # 2) Embeds
    for emb in message.embeds:
        blob = " ".join(
            filter(
                None,
                [
                    emb.title or "",
                    emb.description or "",
                    emb.url or "",
                    getattr(emb.author, "name", "") or "",
                ],
            )
        )
        matched = is_hack_message(blob)
        if matched:
            return matched, "embed text"

    image_attachments = [a for a in message.attachments if _attachment_is_image(a)]
    if not image_attachments:
        return None, ""

    # 3) Simple path: image + any caption that looks suspicious (or any pattern hit)
    caption = (message.content or "").strip()
    if caption:
        # Even short captions with an image can be scam bait
        matched = is_hack_message(caption, allow_short=True)
        if matched:
            return matched, "image + caption"
        # Also flag common short bait words when paired with an image
        short_bait = (
            "nitro", "gift", "free", "claim", "mrbeast", "mr beast",
            "giveaway", "verify", "steam", "roblox",
        )
        lower_cap = caption.lower()
        if any(w in lower_cap for w in short_bait):
            return lower_cap[:80] or "image+bait caption", "image + suspicious caption"

    # 4) Strong path: OCR every image attachment
    for att in image_attachments:
        ocr_text = await _ocr_attachment(att)
        if not ocr_text:
            continue
        matched = is_hack_message(ocr_text, allow_short=True)
        if matched:
            return matched, f"OCR of attachment `{att.filename}`"
        # Log OCR output at debug level for tuning
        log.debug("OCR from %s: %s", att.filename, ocr_text[:200])

    return None, ""


async def handle_hacked_account_message(message: discord.Message) -> bool:
    """If the message matches a hack pattern (text / caption / OCR), strip roles,
    timeout the member, delete the message, and notify the alert channel.
    Returns True if action was taken."""
    if message.guild is None or not isinstance(message.author, discord.Member):
        return False
    if message.author.bot:
        return False
    # Never act on the bot owner
    if BOT_OWNER_ID > 0 and message.author.id == BOT_OWNER_ID:
        return False
    # Respect system on/off
    if not await is_system_enabled("hack_detection"):
        return False

    matched, source = await detect_hack_in_message(message)
    if matched is None:
        return False

    member = message.author
    guild = message.guild
    reason = "Account hacked (auto security detection)"

    # 1. Delete the offending message
    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        log.warning("Could not delete suspected hack message from %s", member.id)

    # 2. Remove all removable roles
    removable = [
        r
        for r in member.roles
        if r != guild.default_role and not r.managed and r < guild.me.top_role
    ]
    roles_removed = 0
    if removable:
        try:
            await member.remove_roles(*removable, reason=reason)
            roles_removed = len(removable)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not remove roles from suspected hacked member %s", member.id)

    # 3. Timeout
    timeout_minutes = max(1, SECURITY_TIMEOUT_MINUTES)
    try:
        await member.timeout(
            timedelta(minutes=timeout_minutes),
            reason=reason,
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not timeout suspected hacked member %s", member.id)

    # 4. Notify alert channel
    alert_channel = None
    if SECURITY_ALERT_CHANNEL_ID > 0:
        alert_channel = await get_text_channel(bot, SECURITY_ALERT_CHANNEL_ID)
    if alert_channel is None and FAMILY_LOG_CHANNEL_ID > 0:
        alert_channel = await get_text_channel(bot, FAMILY_LOG_CHANNEL_ID)

    if alert_channel is not None:
        role_ping = f"<@&{SECURITY_ALERT_ROLE_ID}> " if SECURITY_ALERT_ROLE_ID > 0 else ""
        text = (
            f"{role_ping}I have removed the role of {member.mention} "
            f"and given him timeout of {timeout_minutes} minutes "
            f"reason acc hacked"
        )
        embed = discord.Embed(
            title="🛡️ Auto Security — Compromised Account Detected",
            description=(
                f"**Member:** {member.mention} (`{member.id}`)\n"
                f"**Matched pattern:** `{matched}`\n"
                f"**Detected via:** {source}\n"
                f"**Roles removed:** {roles_removed}\n"
                f"**Timeout:** {timeout_minutes} minutes\n"
                f"**Channel:** {message.channel.mention if hasattr(message.channel, 'mention') else 'DM/unknown'}"
            ),
            colour=Theme.DANGER,
            timestamp=now_local(),
        )
        content_preview = (message.content or "")[:500]
        if content_preview:
            embed.add_field(name="Message content", value=content_preview, inline=False)
        try:
            await alert_channel.send(
                content=text,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    roles=True, users=True, everyone=False
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not send security alert for member %s", member.id)

    log.warning(
        "Hack detection triggered for %s (%s) — pattern=%r source=%s roles_removed=%s",
        member,
        member.id,
        matched,
        source,
        roles_removed,
    )
    return True


@bot.command(name="reloadpatterns")
@_owner_only()
async def reload_security_patterns_command(ctx: commands.Context) -> None:
    """Reload hack-detection patterns from the security_patterns folder
    (text files + OCR of any screenshots in the folder)."""
    count = load_security_patterns(force=True)
    await ctx.reply(
        f"✅ Reloaded **{count}** security detection pattern(s) "
        f"(including OCR from images in `security_patterns/`).",
        mention_author=False,
    )


@bot.command(name="testhack")
@_owner_only()
@commands.guild_only()
async def test_hack_detection_command(ctx: commands.Context, *, sample: str = "") -> None:
    """Owner-only: test whether sample text would be flagged as a hack post.
    Also accepts an attached image — OCR will be run on it.
    Does NOT take action — only reports the match result."""
    # Build a fake-like inspection using current message
    text_match = is_hack_message(sample) if sample else None
    ocr_match = None
    ocr_source = ""
    ocr_preview = ""

    image_atts = [a for a in ctx.message.attachments if _attachment_is_image(a)]
    if image_atts:
        for att in image_atts:
            ocr_text = await _ocr_attachment(att)
            if not ocr_text:
                continue
            ocr_preview = ocr_text[:300]
            ocr_match = is_hack_message(ocr_text, allow_short=True)
            if ocr_match:
                ocr_source = att.filename
                break
            ocr_source = att.filename

    if not sample and not image_atts:
        await ctx.reply(
            "Usage:\n"
            "• `!testhack <sample message text>`\n"
            "• `!testhack` + attach a screenshot to test OCR\n"
            "• `!testhack some caption` + attach image to test caption+image",
            mention_author=False,
        )
        return

    lines = []
    if sample:
        if text_match:
            lines.append(f"🚨 **TEXT MATCH** — pattern: `{text_match}`")
        else:
            lines.append("✅ Text alone would **not** trigger.")
    if image_atts:
        if ocr_match:
            lines.append(
                f"🚨 **OCR MATCH** on `{ocr_source}` — pattern: `{ocr_match}`"
            )
        else:
            lines.append(f"✅ OCR on attachment(s) would **not** trigger.")
        if ocr_preview:
            lines.append(f"**OCR preview:**\n```\n{ocr_preview}\n```")
        if sample and not text_match:
            # caption + image simple path
            short_bait = (
                "nitro", "gift", "free", "claim", "mrbeast", "mr beast",
                "giveaway", "verify", "steam", "roblox",
            )
            if any(w in sample.lower() for w in short_bait):
                lines.append("🚨 **IMAGE + SUSPICIOUS CAPTION** would trigger.")

    await ctx.reply("\n".join(lines), mention_author=False)


# Load patterns once when this module is executed into the shared namespace.
import time  # kept here so other systems don't depend on it at top level

try:
    load_security_patterns()
except Exception:
    log.exception("Failed to load initial security patterns")
