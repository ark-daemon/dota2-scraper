CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    source_id TEXT,
    name TEXT NOT NULL,
    region TEXT,
    rating REAL,
    record TEXT,
    form_5 TEXT,
    form_10 TEXT,
    radiant_win_rate REAL,
    dire_win_rate REAL,
    avg_game_duration_seconds INTEGER,
    avg_net_worth_diff_10 REAL,
    avg_net_worth_diff_15 REAL,
    avg_net_worth_diff_20 REAL,
    total_prize_money REAL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    source_id TEXT,
    ign TEXT NOT NULL,
    real_name TEXT,
    nationality TEXT,
    primary_position INTEGER,
    career_earnings REAL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    source_id TEXT,
    name TEXT NOT NULL,
    tier INTEGER,
    region TEXT,
    prize_pool_total REAL,
    prize_pool_breakdown_json TEXT,
    dpc_points_json TEXT,
    start_date TEXT,
    end_date TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    source_id TEXT,
    tournament_id INTEGER REFERENCES tournaments(id),
    tournament_name TEXT,
    region TEXT,
    team_a_id INTEGER REFERENCES teams(id),
    team_b_id INTEGER REFERENCES teams(id),
    team_a_name TEXT,
    team_b_name TEXT,
    team_a_score INTEGER,
    team_b_score INTEGER,
    series_format TEXT,
    patch_version TEXT,
    scheduled_at_utc TEXT,
    completed_at_utc TEXT,
    head_to_head_all_time TEXT,
    head_to_head_by_tier_json TEXT,
    status TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER REFERENCES matches(id) ON DELETE CASCADE,
    source TEXT,
    source_id TEXT,
    game_number INTEGER,
    radiant_team_id INTEGER REFERENCES teams(id),
    dire_team_id INTEGER REFERENCES teams(id),
    radiant_team_name TEXT,
    dire_team_name TEXT,
    winning_side TEXT,
    duration_seconds INTEGER,
    patch_version TEXT,
    mega_creeps_team TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
    source TEXT,
    source_id TEXT,
    first_pick_team_id INTEGER REFERENCES teams(id),
    first_pick_team_name TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS draft_picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE CASCADE,
    game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
    source TEXT,
    source_id TEXT,
    sequence_index INTEGER,
    phase TEXT,
    action TEXT,
    team_id INTEGER REFERENCES teams(id),
    team_name TEXT,
    side TEXT,
    hero_name TEXT,
    draft_position INTEGER,
    is_first_pick INTEGER,
    is_counter_pick INTEGER,
    hero_patch_win_rate REAL,
    hero_side_win_rate REAL,
    hero_role_win_rate REAL,
    synergy_pairs_json TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS player_game_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
    player_id INTEGER REFERENCES players(id),
    team_id INTEGER REFERENCES teams(id),
    source TEXT,
    source_id TEXT,
    player_ign TEXT,
    team_name TEXT,
    hero_name TEXT,
    position INTEGER,
    lane_assignment TEXT,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    kda REAL,
    kill_participation_pct REAL,
    gpm INTEGER,
    xpm INTEGER,
    net_worth_end INTEGER,
    net_worth_vs_opposing_position INTEGER,
    last_hits INTEGER,
    denies INTEGER,
    hero_damage INTEGER,
    tower_damage INTEGER,
    hero_healing INTEGER,
    observer_wards_placed INTEGER,
    observer_wards_destroyed INTEGER,
    sentry_wards_placed INTEGER,
    sentry_wards_destroyed INTEGER,
    camps_stacked INTEGER,
    ancient_stacks INTEGER,
    teamfight_participation_pct REAL,
    final_items_json TEXT,
    backpack_items_json TEXT,
    neutral_item TEXT,
    skill_build_json TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS game_timelines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
    source TEXT,
    source_id TEXT,
    gold_advantage_10 INTEGER,
    gold_advantage_15 INTEGER,
    gold_advantage_20 INTEGER,
    xp_advantage_10 INTEGER,
    xp_advantage_15 INTEGER,
    xp_advantage_20 INTEGER,
    roshan_kills_json TEXT,
    first_blood_json TEXT,
    barracks_destroyed_json TEXT,
    tower_kills_json TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS rosters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER REFERENCES teams(id),
    player_id INTEGER REFERENCES players(id),
    source TEXT,
    source_id TEXT,
    team_name TEXT,
    player_ign TEXT,
    real_name TEXT,
    position INTEGER,
    nationality TEXT,
    join_date TEXT,
    leave_date TEXT,
    is_active INTEGER,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER REFERENCES teams(id),
    source TEXT,
    source_id TEXT,
    team_name TEXT,
    ign TEXT,
    real_name TEXT,
    role TEXT,
    nationality TEXT,
    join_date TEXT,
    leave_date TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS standins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER REFERENCES teams(id),
    player_id INTEGER REFERENCES players(id),
    tournament_id INTEGER REFERENCES tournaments(id),
    match_id INTEGER REFERENCES matches(id),
    source TEXT,
    source_id TEXT,
    team_name TEXT,
    player_ign TEXT,
    replaced_player_ign TEXT,
    tournament_name TEXT,
    match_name TEXT,
    start_date TEXT,
    end_date TEXT,
    reason TEXT,
    move_type TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS earnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    source_id TEXT,
    player_id INTEGER REFERENCES players(id),
    team_id INTEGER REFERENCES teams(id),
    tournament_id INTEGER REFERENCES tournaments(id),
    player_ign TEXT,
    team_name TEXT,
    tournament_name TEXT,
    placement TEXT,
    amount REAL,
    currency TEXT,
    earned_at TEXT,
    is_ti_history INTEGER,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(completed_at_utc, scheduled_at_utc);
CREATE INDEX IF NOT EXISTS idx_games_match ON games(match_id);
CREATE INDEX IF NOT EXISTS idx_draft_picks_game ON draft_picks(game_id);
CREATE INDEX IF NOT EXISTS idx_player_stats_game ON player_game_stats(game_id);
CREATE INDEX IF NOT EXISTS idx_rosters_team ON rosters(team_id);
CREATE INDEX IF NOT EXISTS idx_standins_team ON standins(team_id);
CREATE INDEX IF NOT EXISTS idx_earnings_player ON earnings(player_id);
