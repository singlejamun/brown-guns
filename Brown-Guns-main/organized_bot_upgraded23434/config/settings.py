# === Configuration & constants ===
# Source lines 1-141 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

from __future__ import annotations

import asyncio
from collections import defaultdict
import io
import json
import logging
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int = 0) -> int:
    """Read an int env var safely.

    - Empty / missing → default
    - Pure digits → int value
    - Value that looks like another env var name (e.g. CONTROL_SERVER_LOG_CHANNEL_ID)
      → resolve that env var (one level) or fall back to default
    - Anything else → default (never crash on bad Railway config)
    """
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return int(default)
    value = str(raw).strip()
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    # User may have set RP_TICKET_HC_LOG_CHANNEL_ID=CONTROL_SERVER_LOG_CHANNEL_ID
    # meaning "use the same value as that other variable".
    if value.isidentifier() and value.upper() == value:
        ref = os.getenv(value)
        if ref is not None and str(ref).strip().lstrip("-").isdigit():
            return int(str(ref).strip())
        # Also try the already-parsed Python name if it exists later — handled by callers
        return int(default)
    return int(default)


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
BOT_OWNER_ID = _env_int("BOT_OWNER_ID", 0)
GUILD_ID = _env_int("GUILD_ID", 0)
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "family_bot.sqlite3"))

REGISTRATION_CHANNEL_ID = _env_int("REGISTRATION_CHANNEL_ID", 0)
INFORMAL_ROSTER_ROLE_ID = _env_int("INFORMAL_ROSTER_ROLE_ID", 0)
TURFER_ROLE_ID = _env_int("TURFER_ROLE_ID", 0)
FAMILY_MEMBER_ROLE_ID = _env_int("FAMILY_MEMBER_ROLE_ID", 0)
STATE_ROSTER_ROLE_ID = _env_int("STATE_ROSTER_ROLE_ID", 0)
STATE_LEADER_ROLE_ID = _env_int("STATE_LEADER_ROLE_ID", 0)

ROLE_REQUEST_CHANNEL_ID = _env_int("ROLE_REQUEST_CHANNEL_ID", 0)
ROLE_REVIEW_CHANNEL_ID = _env_int("ROLE_REVIEW_CHANNEL_ID", 0)
EVENT_PING_CHANNEL_ID = _env_int("EVENT_PING_CHANNEL_ID", 0)
STATE_REGISTER_CHANNEL_ID = _env_int("STATE_REGISTER_CHANNEL_ID", 0)
COMPLAINT_CHANNEL_ID = _env_int("COMPLAINT_CHANNEL_ID", 0)
COMPLAINT_REVIEW_CHANNEL_ID = _env_int("COMPLAINT_REVIEW_CHANNEL_ID", 0)
PUNISHMENT_BOARD_CHANNEL_ID = _env_int("PUNISHMENT_BOARD_CHANNEL_ID", 0)
# Optional: separate channel for punishment appeals. If unset, appeals go to
# COMPLAINT_REVIEW_CHANNEL_ID like before.
PUNISHMENT_APPEAL_CHANNEL_ID = _env_int("PUNISHMENT_APPEAL_CHANNEL_ID", 0)
# POV requests are reviewed in this channel. If unset, COMPLAINT_REVIEW_CHANNEL_ID is used.
POV_REVIEW_CHANNEL_ID = _env_int("POV_REVIEW_CHANNEL_ID", 0)
# How long the suspect has to send the POV link after a request is accepted.
POV_RESPONSE_HOURS = 4
# Jailer role: only members with this role can punish, remove strikes and review
# complaints / POV requests. Set it in Discord with  !jailer @role  (saved in the
# database and overrides this env value). Optional env default: JAILER_ROLE_ID.
JAILER_ROLE_ID = _env_int("JAILER_ROLE_ID", 0)

# Private database/control server
CONTROL_GUILD_ID = _env_int("CONTROL_GUILD_ID", 0)
CONTROL_COMMAND_CHANNEL_ID = _env_int("CONTROL_COMMAND_CHANNEL_ID", 0)
CONTROL_SERVER_LOG_CHANNEL_ID = _env_int("CONTROL_SERVER_LOG_CHANNEL_ID", 0)
CONTROL_MEMBER_INFO_CHANNEL_ID = _env_int("CONTROL_MEMBER_INFO_CHANNEL_ID", 0)
CONTROL_COMPLAINT_LOG_CHANNEL_ID = _env_int("CONTROL_COMPLAINT_LOG_CHANNEL_ID", 0)
CONTROL_RANK_LOG_CHANNEL_ID = _env_int("CONTROL_RANK_LOG_CHANNEL_ID", 0)
MAIN_EVENT_PANEL_CHANNEL_ID = _env_int("MAIN_EVENT_PANEL_CHANNEL_ID", 0)

# If unset or invalid, fall back to CONTROL_SERVER_LOG_CHANNEL_ID
_rp_hc = _env_int("RP_TICKET_HC_LOG_CHANNEL_ID", 0)
RP_TICKET_HC_LOG_CHANNEL_ID = _rp_hc if _rp_hc else CONTROL_SERVER_LOG_CHANNEL_ID

ADVISER_ROLE_ID = _env_int("ADVISER_ROLE_ID", 0)

_family_log = _env_int("FAMILY_LOG_CHANNEL_ID", 0)
FAMILY_LOG_CHANNEL_ID = _family_log if _family_log else RP_TICKET_HC_LOG_CHANNEL_ID

_rp_reminder = _env_int("RP_TICKET_REMINDER_CHANNEL_ID", 0)
RP_TICKET_REMINDER_CHANNEL_ID = _rp_reminder if _rp_reminder else EVENT_PING_CHANNEL_ID

# Anti-hack / compromised account detection (MrBeast-style scam messages)
SECURITY_ALERT_CHANNEL_ID = _env_int("SECURITY_ALERT_CHANNEL_ID", 0)
SECURITY_ALERT_ROLE_ID = _env_int("SECURITY_ALERT_ROLE_ID", 0)
SECURITY_TIMEOUT_MINUTES = _env_int("SECURITY_TIMEOUT_MINUTES", 180)

SYSTEM_NAMES = {
    "informal_registration": "Informal Registration",
    "event_panel": "Event Panel",
    "rp_ticket_reminder": "RP Ticket Reminder",
    "hack_detection": "Hack Detection",
}

# Welcome system in the main server
WELCOME_CHANNEL_ID = _env_int("WELCOME_CHANNEL_ID", 0)
RULES_CHANNEL_ID = _env_int("RULES_CHANNEL_ID", 0)
GENERAL_CHAT_CHANNEL_ID = _env_int("GENERAL_CHAT_CHANNEL_ID", 0)
WELCOME_BANNER_URL = os.getenv("WELCOME_BANNER_URL", "").strip()

ALT_ACCOUNT_MAX_DAYS = _env_int("ALT_ACCOUNT_MAX_DAYS", 7)

STAFF_ROLE_IDS = {
    int(value.strip())
    for value in os.getenv("STAFF_ROLE_IDS", "").split(",")
    if value.strip().isdigit()
}

HIGH_COMMAND_ROLE_IDS = {
    int(value.strip())
    for value in os.getenv("HIGH_COMMAND_ROLE_IDS", "").split(",")
    if value.strip().isdigit()
}

CONTROL_ADMIN_ROLE_IDS = {
    int(value.strip())
    for value in os.getenv("CONTROL_ADMIN_ROLE_IDS", "").split(",")
    if value.strip().isdigit()
}


# Members granted full private control-server access through !controlaccess.
# IDs are persisted in SQLite and loaded when the bot starts.
CONTROL_ACCESS_USER_IDS: set[int] = set()

WARNING_ROLE_IDS = {
    1: _env_int("WARN_1_ROLE_ID", 0),
    2: _env_int("WARN_2_ROLE_ID", 0),
    3: _env_int("WARN_3_ROLE_ID", 0),
}

RANK_ROLE_IDS = {
    rank: _env_int(f"RANK_{rank}_ROLE_ID", 0)
    for rank in range(1, 9)
}

RANK_9_ROLE_ID = _env_int("RANK_9_ROLE_ID", 0)
RANK_10_ROLE_ID = _env_int("RANK_10_ROLE_ID", 0)

RANK_NAMES = {
    1: "Newbie",
    2: "Suspended",
    3: "Associated",
    4: "Fighter",
    5: "Turfer",
    6: "Trusted",
    7: "Man Of Hood",
    8: "Captain",
}

MAX_REGISTRATIONS = 10
MAX_WAITING_LIST = 5
REGISTRATION_CLOSE_MINUTE = 13
ROLE_REMOVE_MINUTE = 40
MAX_ACTIVE_WARNINGS = 3
WARNING_DURATION_DAYS = 7

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    # Route logs to stdout instead of the default stderr. Platforms like
    # Railway colour anything on stderr red regardless of its actual
    # level, which is why every INFO/WARNING line was showing red.
    stream=sys.stdout,
)
log = logging.getLogger("grand-rp-family-bot")

