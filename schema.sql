-- Workshop Diagnostic Network — database schema
-- SQLite. Created automatically on first run by app.py.

CREATE TABLE IF NOT EXISTS workshops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    city TEXT,
    contact_email TEXT,
    phone TEXT,
    tier TEXT NOT NULL DEFAULT 'free',        -- free, basic, premium
    status TEXT NOT NULL DEFAULT 'active',    -- active, pending, suspended
    billing_notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workshop_id INTEGER,                       -- NULL for platform_admin
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner',        -- owner, technician, platform_admin
    created_at TEXT NOT NULL,
    FOREIGN KEY (workshop_id) REFERENCES workshops (id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS tips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_number TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    category TEXT,
    subcategory TEXT,
    function_group TEXT,
    control_unit TEXT,
    fault_codes TEXT,
    model_series TEXT,
    symptom TEXT,
    diagnosis TEXT,
    fix TEXT,
    notes TEXT,
    source TEXT DEFAULT 'Community',
    workshop_id INTEGER,                       -- contributing workshop (kept private, not shown publicly)
    created_by TEXT,
    created_at TEXT NOT NULL,
    source_case_id INTEGER,
    confirm_count INTEGER DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'pending', -- pending, published (admin must approve before it's visible network-wide)
    FOREIGN KEY (source_case_id) REFERENCES cases (id),
    FOREIGN KEY (workshop_id) REFERENCES workshops (id)
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workshop_id INTEGER NOT NULL,
    case_number TEXT UNIQUE NOT NULL,
    technician TEXT NOT NULL,
    case_date TEXT NOT NULL,
    vehicle_model TEXT,
    vin TEXT,
    mileage TEXT,
    function_group TEXT,
    control_unit TEXT,
    fault_codes TEXT,
    symptom TEXT NOT NULL,
    diagnosis_steps TEXT,
    root_cause TEXT,
    fix_applied TEXT,
    parts_used TEXT,
    time_spent_hours REAL,
    status TEXT DEFAULT 'Open',
    linked_tip_id INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (linked_tip_id) REFERENCES tips (id),
    FOREIGN KEY (workshop_id) REFERENCES workshops (id)
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workshop_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    vehicle_model TEXT,
    status TEXT NOT NULL DEFAULT 'open',       -- open, answered, closed
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workshop_id) REFERENCES workshops (id)
);

CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    sender_role TEXT NOT NULL,                 -- workshop, admin
    sender_name TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
);

CREATE INDEX IF NOT EXISTS idx_tips_fault_codes ON tips (fault_codes);
CREATE INDEX IF NOT EXISTS idx_cases_workshop ON cases (workshop_id);
CREATE INDEX IF NOT EXISTS idx_cases_fault_codes ON cases (fault_codes);
CREATE INDEX IF NOT EXISTS idx_tickets_workshop ON tickets (workshop_id);
CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages (ticket_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
