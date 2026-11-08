# === Database layer + db instance ===
# Loaded into shared namespace by bot.py — do not run this file alone.
#
# Upgrades over the original layer:
#   * WAL journal + busy_timeout + synchronous=NORMAL  (no "database is locked")
#   * every query runs in a worker thread  (SQLite never blocks the Discord heartbeat)
#   * connections are always closed  (the old `with connect()` leaked them)
#   * indexes on every column the bot filters by
#   * schema version tracked with PRAGMA user_version
#   * all tables live here (bot_settings, lol_access, rp_list_* moved in)
#   * the old startup DROP TABLE is now a one-time migration
#   * Railway persistence check (warns / refuses when the DB is on temporary disk)
#   * consistent online backups with rotation + integrity check

SCHEMA_VERSION = 2


class _ClosingConnection(sqlite3.Connection):
    """`with db.connect() as conn:` commits/rolls back AND closes the connection.

    A plain sqlite3 connection only commits on `with` exit and stays open, which
    leaks one file handle per query. Existing callers keep working unchanged.
    """

    def __exit__(self, exc_type, exc, tb):  # type: ignore[override]
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def _check_storage_location(path: Path) -> None:
    """Warn loudly (or refuse to start) if the DB would live on Railway's
    temporary disk, which is wiped on every deploy."""
    on_railway = any(
        os.getenv(name)
        for name in (
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_ENVIRONMENT_NAME",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_SERVICE_ID",
        )
    )
    if not on_railway:
        return

    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    problem = None
    if not mount:
        problem = (
            "this Railway service has NO Volume attached, so the database is "
            "erased on every redeploy"
        )
    else:
        try:
            path.resolve().relative_to(Path(mount).resolve())
        except ValueError:
            problem = (
                f"DATABASE_PATH ({path}) is outside the Volume mounted at "
                f"{mount}, so it is erased on every redeploy"
            )

    if problem is None:
        return

    message = (
        f"DATABASE NOT PERSISTENT: {problem}. Add a Railway Volume and set "
        "DATABASE_PATH=/data/family_bot.sqlite3."
    )
    log.critical(message)
    if os.getenv("DB_REQUIRE_PERSISTENT", "").strip().lower() in ("1", "true", "yes"):
        raise RuntimeError(message)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        _check_storage_location(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir = Path(
            os.getenv("DB_BACKUP_DIR", "").strip() or (self.path.parent / "backups")
        )
        self.lock = asyncio.Lock()
        self._initialize()
        self._migrate()
        self._create_indexes()
        self._set_schema_version()

    # ------------------------------------------------------------------
    # connections
    # ------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=15,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA temp_store = MEMORY")
        return connection

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------
    def _initialize(self) -> None:
        with self.connect() as database:
            # WAL is stored in the file itself; setting it once is enough.
            database.execute("PRAGMA journal_mode = WAL")

            # One-time cleanup of tables that no longer exist in the bot.
            # (Used to run on EVERY startup; now only on databases older than v1.)
            version = int(database.execute("PRAGMA user_version").fetchone()[0])
            if version < 1:
                database.executescript(
                    """
                    DROP TABLE IF EXISTS forum_watchlist;
                    DROP TABLE IF EXISTS forum_seen_threads;
                    """
                )

            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS registration_sessions (
                    slot_key TEXT PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    closed INTEGER NOT NULL DEFAULT 0,
                    roles_removed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS registration_members (
                    slot_key TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('registered','waiting')),
                    position INTEGER NOT NULL,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY(slot_key, user_id),
                    FOREIGN KEY(slot_key)
                        REFERENCES registration_sessions(slot_key)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS role_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    requester_id INTEGER NOT NULL,
                    ingame_name TEXT,
                    ingame_id TEXT,
                    invited_by TEXT,
                    requested_rank INTEGER NOT NULL,
                    proof_url TEXT,
                    original_channel_id INTEGER,
                    original_message_id INTEGER,
                    review_channel_id INTEGER NOT NULL,
                    review_message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewed_by INTEGER,
                    reviewed_at TEXT,
                    denial_reason TEXT,
                    nickname_changed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS punishment_panels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    target_user_id INTEGER NOT NULL,
                    created_by INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 3),
                    role_id INTEGER NOT NULL,
                    reason TEXT,
                    issued_by INTEGER NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS punishment_board_posts (
                    warning_id INTEGER PRIMARY KEY,
                    panel_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS punishment_appeals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    punishment_panel_id INTEGER NOT NULL,
                    warning_id INTEGER NOT NULL,
                    appellant_user_id INTEGER NOT NULL,
                    appellant_name TEXT NOT NULL,
                    appellant_game_id TEXT NOT NULL,
                    punished_by INTEGER NOT NULL,
                    punishment_reason TEXT NOT NULL,
                    appeal_description TEXT NOT NULL,
                    proof TEXT,
                    review_channel_id INTEGER NOT NULL,
                    review_message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewed_by INTEGER,
                    reviewed_at TEXT,
                    rejection_reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS state_control_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    final_message_id INTEGER,
                    created_by INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS state_control_members (
                    session_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('registered','waiting')),
                    position INTEGER NOT NULL,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS state_team_members (
                    session_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS complaints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    complainant_user_id INTEGER NOT NULL,
                    complainant_game_id TEXT NOT NULL,
                    accused_game_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    proof TEXT,
                    review_channel_id INTEGER NOT NULL,
                    review_message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewed_by INTEGER,
                    reviewed_at TEXT,
                    resolution_reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pov_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    requester_user_id INTEGER NOT NULL,
                    requester_game_id TEXT NOT NULL,
                    suspect_game_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    event_time TEXT,
                    reason TEXT NOT NULL,
                    review_channel_id INTEGER NOT NULL,
                    review_message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    suspect_user_id INTEGER,
                    dm_message_id INTEGER,
                    deadline_at TEXT,
                    pov_link TEXT,
                    reviewed_by INTEGER,
                    reviewed_at TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS member_invites (
                    guild_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    inviter_id INTEGER,
                    invite_code TEXT,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, member_id)
                );

                CREATE TABLE IF NOT EXISTS control_access (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    granted_by INTEGER NOT NULL,
                    granted_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS system_status (
                    system_name TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rp_ticket_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    event_channel_id INTEGER NOT NULL,
                    started_by INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    result_due_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_status',
                    status_message_id INTEGER,
                    decided_by INTEGER,
                    decided_at TEXT,
                    next_reminder_at TEXT,
                    last_reminder_message_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS rp_ticket_collections (
                    session_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    collected_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, user_id),
                    FOREIGN KEY(session_id) REFERENCES rp_ticket_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS rp_ticket_reminders (
                    session_id INTEGER NOT NULL,
                    reminder_at TEXT NOT NULL,
                    reminder_message_id INTEGER,
                    claim_prompt_at TEXT NOT NULL,
                    claim_message_id INTEGER,
                    PRIMARY KEY(session_id, reminder_at),
                    FOREIGN KEY(session_id) REFERENCES rp_ticket_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS rp_ticket_hourly_collections (
                    session_id INTEGER NOT NULL,
                    reminder_at TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    collected_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, reminder_at, user_id),
                    FOREIGN KEY(session_id, reminder_at)
                        REFERENCES rp_ticket_reminders(session_id, reminder_at)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS rp_ticket_log_panels (
                    session_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES rp_ticket_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS rp_ticket_missed_log (
                    session_id INTEGER NOT NULL,
                    reminder_at TEXT NOT NULL,
                    prompt_message_id INTEGER,
                    resolution TEXT CHECK(resolution IN ('collected_late', 'missed')),
                    logged_by INTEGER,
                    logged_at TEXT,
                    PRIMARY KEY(session_id, reminder_at),
                    FOREIGN KEY(session_id, reminder_at)
                        REFERENCES rp_ticket_reminders(session_id, reminder_at)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS auto_data_categories (
                    guild_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    category_id INTEGER NOT NULL,
                    PRIMARY KEY(guild_id, key)
                );

                CREATE TABLE IF NOT EXISTS auto_data_channels (
                    guild_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, key)
                );

                -- Moved here from rank_request.py / lol.py / rp_list.py so the
                -- whole schema lives in one file. Their own CREATE ... IF NOT
                -- EXISTS calls are now harmless no-ops.
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS lol_access (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    granted_by INTEGER NOT NULL,
                    granted_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, user_id)
                );

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

    def _migrate(self) -> None:
        """Additive, idempotent column migrations for tables that already
        existed on disk (e.g. a live Railway volume) before these columns
        were introduced. Safe to run on every startup — only ever ADDs a
        missing column, never drops or renames anything."""
        migrations = [
            ("rp_ticket_reminders", "escalation_message_id", "INTEGER"),
            ("rp_ticket_reminders", "escalated_at", "TEXT"),
            ("rp_ticket_hourly_collections", "late", "INTEGER NOT NULL DEFAULT 0"),
            ("warnings", "end_reason", "TEXT"),
            ("warnings", "ended_at", "TEXT"),
            ("warnings", "ended_by", "INTEGER"),
            ("warnings", "end_note", "TEXT"),
        ]
        with self.connect() as database:
            for table, column, coltype in migrations:
                existing_columns = {
                    row[1]
                    for row in database.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if column not in existing_columns:
                    database.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"
                    )
            database.commit()

    def _create_indexes(self) -> None:
        """Indexes for every non-primary-key lookup the bot performs.
        All are IF NOT EXISTS, so this is safe on every startup and only
        costs time the very first run on a large existing database."""
        with self.connect() as database:
            database.executescript(
                """
                -- warning expiry loop + "active warnings of this member"
                CREATE INDEX IF NOT EXISTS idx_warnings_active_expiry
                    ON warnings(active, expires_at);
                CREATE INDEX IF NOT EXISTS idx_warnings_member
                    ON warnings(guild_id, user_id, active, expires_at);

                CREATE INDEX IF NOT EXISTS idx_punishment_panels_target
                    ON punishment_panels(guild_id, target_user_id);
                CREATE INDEX IF NOT EXISTS idx_punishment_appeals_warning
                    ON punishment_appeals(warning_id);

                CREATE INDEX IF NOT EXISTS idx_role_requests_status
                    ON role_requests(status);
                CREATE INDEX IF NOT EXISTS idx_role_requests_requester
                    ON role_requests(guild_id, requester_id, status);

                CREATE INDEX IF NOT EXISTS idx_complaints_status
                    ON complaints(status);
                CREATE INDEX IF NOT EXISTS idx_complaints_complainant
                    ON complaints(guild_id, complainant_user_id);

                -- pov expiry loop
                CREATE INDEX IF NOT EXISTS idx_pov_requests_status_deadline
                    ON pov_requests(status, deadline_at);

                -- rp ticket schedulers
                CREATE INDEX IF NOT EXISTS idx_rp_ticket_sessions_status
                    ON rp_ticket_sessions(status);
                CREATE INDEX IF NOT EXISTS idx_rp_ticket_sessions_due
                    ON rp_ticket_sessions(result_due_at);

                CREATE INDEX IF NOT EXISTS idx_state_control_sessions_status
                    ON state_control_sessions(guild_id, status);
                CREATE INDEX IF NOT EXISTS idx_registration_sessions_closed
                    ON registration_sessions(closed);

                CREATE INDEX IF NOT EXISTS idx_rp_list_panels_message
                    ON rp_list_panels(message_id);
                CREATE INDEX IF NOT EXISTS idx_auto_data_channels_channel
                    ON auto_data_channels(guild_id, channel_id);
                CREATE INDEX IF NOT EXISTS idx_member_invites_inviter
                    ON member_invites(guild_id, inviter_id);
                """
            )
            database.commit()

    def _set_schema_version(self) -> None:
        with self.connect() as database:
            database.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")

    # ------------------------------------------------------------------
    # async query helpers (same signatures as before)
    # ------------------------------------------------------------------
    def _run_execute(self, query: str, parameters: tuple) -> int:
        with self.connect() as database:
            cursor = database.execute(query, parameters)
            return int(cursor.lastrowid or 0)

    def _run_execute_count(self, query: str, parameters: tuple) -> int:
        with self.connect() as database:
            cursor = database.execute(query, parameters)
            return int(cursor.rowcount)

    def _run_fetchone(self, query: str, parameters: tuple):
        with self.connect() as database:
            return database.execute(query, parameters).fetchone()

    def _run_fetchall(self, query: str, parameters: tuple):
        with self.connect() as database:
            return database.execute(query, parameters).fetchall()

    async def execute(self, query: str, parameters: tuple = ()) -> int:
        async with self.lock:
            return await asyncio.to_thread(self._run_execute, query, parameters)

    async def execute_count(self, query: str, parameters: tuple = ()) -> int:
        """Like execute(), but returns how many rows were changed. Used for
        'claim' updates (UPDATE ... WHERE status = 'pending') so that two
        reviewers clicking at the same moment can't both win."""
        async with self.lock:
            return await asyncio.to_thread(self._run_execute_count, query, parameters)

    async def fetchone(
        self,
        query: str,
        parameters: tuple = (),
    ) -> sqlite3.Row | None:
        async with self.lock:
            return await asyncio.to_thread(self._run_fetchone, query, parameters)

    async def fetchall(
        self,
        query: str,
        parameters: tuple = (),
    ) -> list[sqlite3.Row]:
        async with self.lock:
            return await asyncio.to_thread(self._run_fetchall, query, parameters)

    # ------------------------------------------------------------------
    # health + backups
    # ------------------------------------------------------------------
    def health_report(self) -> dict:
        """Synchronous integrity check (call via asyncio.to_thread)."""
        with self.connect() as database:
            quick = [str(r[0]) for r in database.execute("PRAGMA quick_check").fetchall()]
            fk_problems = database.execute("PRAGMA foreign_key_check").fetchall()
            version = int(database.execute("PRAGMA user_version").fetchone()[0])
            journal = str(database.execute("PRAGMA journal_mode").fetchone()[0])
        return {
            "ok": quick == ["ok"] and not fk_problems,
            "quick_check": quick,
            "foreign_key_problems": len(fk_problems),
            "schema_version": version,
            "journal_mode": journal,
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }

    def list_backups(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob("family_bot-*.sqlite3"))

    def backup_now(self, keep: int = 14) -> Path:
        """Take a consistent online snapshot using SQLite's backup API, verify
        it, then rotate old snapshots. Synchronous — call via asyncio.to_thread."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(TIMEZONE).strftime("%Y%m%d-%H%M%S")
        final = self.backup_dir / f"family_bot-{stamp}.sqlite3"
        partial = final.with_name(final.name + ".partial")

        source = sqlite3.connect(self.path, timeout=30)
        target = sqlite3.connect(partial)
        try:
            source.backup(target)
            result = target.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"backup failed integrity check: {result}")
        except Exception:
            target.close()
            source.close()
            partial.unlink(missing_ok=True)
            raise
        target.close()
        source.close()
        os.replace(partial, final)

        for old in self.list_backups()[: -max(1, int(keep))]:
            old.unlink(missing_ok=True)
        return final


db = Database(DATABASE_PATH)
