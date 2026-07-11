"""
Workshop Diagnostic Network
----------------------------
A multi-tenant SaaS: independent Mercedes-Benz specialist workshops sign up,
subscribe (Free / Basic / Premium), and get:

  - A SHARED community "Tips" knowledge base, built from every subscribing
    workshop's real diagnostic cases (anonymized - contributing workshop is
    never shown to other subscribers).
  - A PRIVATE case log per workshop - only that workshop's own technicians
    see their own cases. Resolved cases can be "promoted" into a shared Tip.
  - Tier gating: Free can browse the Tips list but not full diagnosis/fix
    detail. Basic unlocks full Tips + private case logging. Premium adds
    live in-app technical support (a chat/ticket thread with your team).
  - A platform admin panel (you) to activate/upgrade/suspend workshops
    (billing is handled manually/outside the app for now) and to answer
    support tickets from Premium workshops.

Zero external dependencies - only the Python standard library. This keeps
it deployable on almost any free/cheap Python host without dependency
headaches. See README.md for how to put this on a real, publicly reachable
host so workshops outside your building can sign up and log in.

Run locally:
    python3 app.py
Then open http://localhost:5000
"""

import csv
import hashlib
import hmac
import html as html_lib
import io
import os
import secrets
import socket
import sqlite3
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

# ---------------------------------------------------------------------------
# Configuration - edit freely
# ---------------------------------------------------------------------------

PLATFORM_NAME = "Workshop Diagnostic Network"
PLATFORM_TAGLINE = "A shared diagnostic knowledge base for independent Mercedes-Benz workshops."
ADMIN_SEED_EMAIL = "syakirbadry@gmail.com"
ADMIN_SEED_NAME = "Syakir"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DB_PATH can be overridden with an environment variable, e.g. pointing at a
# mounted persistent disk/volume on a host like Render or Railway, so the
# database survives redeploys. Defaults to sitting right next to app.py,
# which is fine for local use or a plain VPS with a normal filesystem.
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "workshop_network.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.environ.get("PORT", 5000))

SYMPTOM_CATEGORIES = [
    "Overall vehicle", "Lighting", "Power generation", "Power transmission",
    "Chassis/suspension", "Body", "Communication/information", "Electric drive/hybrid",
]

FUNCTION_GROUPS = [
    "08 - Electric drive, hybrid drive",
    "18 - Engine lubrication, engine oil cooling",
    "27 - Automatic transmission",
    "32 - Suspension",
    "33 - Front axle",
    "35 - Rear axle",
    "40 - Wheels, chassis alignment check",
    "42 - Brakes - hydraulic and mechanical systems",
    "54 - Instrument cluster / display",
    "82 - Audio, navigation, telephone, combined instrument",
    "91 - Seats",
]

CASE_STATUSES = ["Open", "In Progress", "Resolved", "Unresolved - escalate"]

TIER_RANK = {"free": 0, "basic": 1, "premium": 2}
TIER_LABEL = {"free": "Free", "basic": "Basic", "premium": "Premium"}
TIER_PRICE = {"free": "RM 0/mo", "basic": "RM 99/mo", "premium": "RM 249/mo"}
WORKSHOP_STATUSES = ["active", "pending", "suspended"]

SESSION_DAYS = 14


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def init_db():
    first_run = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    if first_run:
        seed(conn)
    conn.close()


def hash_password(password, salt_hex=None):
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return digest.hex(), salt.hex()


def verify_password(password, hash_hex, salt_hex):
    check, _ = hash_password(password, salt_hex)
    return hmac.compare_digest(check, hash_hex)


def next_number(conn, table, prefix):
    n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] + 1
    return f"{prefix}-{n:04d}"


def seed(conn):
    ts = now_str()

    # Platform admin account. Password is randomly generated and printed to
    # the console (and saved to ADMIN_CREDENTIALS.txt) on first run only.
    admin_password = secrets.token_urlsafe(9)
    h, s = hash_password(admin_password)
    conn.execute(
        "INSERT INTO users (workshop_id, name, email, password_hash, password_salt, role, created_at) "
        "VALUES (NULL, ?, ?, ?, ?, 'platform_admin', ?)",
        (ADMIN_SEED_NAME, ADMIN_SEED_EMAIL, h, s, ts),
    )
    conn.commit()
    creds_path = os.path.join(BASE_DIR, "ADMIN_CREDENTIALS.txt")
    with open(creds_path, "w") as f:
        f.write(
            f"Platform admin login for {PLATFORM_NAME}\n"
            f"Generated on first run: {ts}\n\n"
            f"URL:      /login\n"
            f"Email:    {ADMIN_SEED_EMAIL}\n"
            f"Password: {admin_password}\n\n"
            f"Change this password by logging in and updating it directly in the\n"
            f"database, or delete this workshop_network.db file and restart to\n"
            f"re-seed (this wipes all data - only do that before going live).\n"
        )
    print("=" * 64)
    print(f"First run: platform admin account created.")
    print(f"  Email:    {ADMIN_SEED_EMAIL}")
    print(f"  Password: {admin_password}")
    print(f"  (also saved to ADMIN_CREDENTIALS.txt - keep this private)")
    print("=" * 64)

    # A demo workshop + a few sample tips, so the platform doesn't look empty
    # for the first visitor / for your own demo purposes.
    conn.execute(
        "INSERT INTO workshops (name, city, contact_email, phone, tier, status, billing_notes, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("Demo Workshop (sample data)", "Kuala Lumpur", "demo@example.com", "", "premium", "active",
         "Seed/demo account - safe to suspend or delete.", ts),
    )
    demo_workshop_id = conn.execute("SELECT id FROM workshops WHERE name = 'Demo Workshop (sample data)'").fetchone()["id"]
    h2, s2 = hash_password("demo1234")
    conn.execute(
        "INSERT INTO users (workshop_id, name, email, password_hash, password_salt, role, created_at) "
        "VALUES (?,?,?,?,?, 'owner', ?)",
        (demo_workshop_id, "Demo Owner", "demo@example.com", h2, s2, ts),
    )

    sample_tips = [
        dict(topic_number="WN-32-0001", title="Front axle creak over speed bumps - check strut top mount",
             category="Chassis/suspension", function_group="32 - Suspension", control_unit="", fault_codes="",
             model_series="W205, W213",
             symptom="Creaking/knocking noise from front suspension over bumps or slow steering input.",
             diagnosis="Road test to confirm noise, isolate side, inspect strut top mounts and sway bar end links with vehicle on lift.",
             fix="Replaced worn strut top mount bearing. Noise gone on re-test.",
             notes="Sample entry - community-contributed, not an official Mercedes-Benz publication."),
        dict(topic_number="WN-27-0002", title="Harsh 1-2 gear shift after cold start",
             category="Power transmission", function_group="27 - Automatic transmission", control_unit="Transmission control unit",
             fault_codes="", model_series="W167, C167",
             symptom="Jolt/harsh shift 1-2 only in first few minutes after cold start, smooths out once warm.",
             diagnosis="Checked transmission fluid condition/level, pulled adaptation values, reviewed fault memory (none stored).",
             fix="Performed transmission adaptation reset per service procedure.",
             notes="Sample entry - community-contributed."),
        dict(topic_number="WN-54-0003", title="Instrument cluster blank on cold mornings",
             category="Communication/information", function_group="54 - Instrument cluster / display", control_unit="Instrument cluster",
             fault_codes="", model_series="W213, W223",
             symptom="Cluster stays blank for 5-10 seconds after start on cold days, then boots normally.",
             diagnosis="Checked battery/ground connections, cluster software version, no fault codes stored during event.",
             fix="Cleaned and re-torqued battery terminal; issue not reproduced after 1 week follow-up.",
             notes="Sample entry - community-contributed."),
    ]
    for t in sample_tips:
        conn.execute(
            """INSERT INTO tips (topic_number, title, category, subcategory, function_group, control_unit,
                fault_codes, model_series, symptom, diagnosis, fix, notes, source, workshop_id, created_by,
                created_at, source_case_id, confirm_count)
               VALUES (:topic_number, :title, :category, '', :function_group, :control_unit, :fault_codes,
                :model_series, :symptom, :diagnosis, :fix, :notes, 'Community', :workshop_id, 'Community',
                :created_at, NULL, 0)""",
            dict(t, workshop_id=demo_workshop_id, created_at=ts),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Small HTML helpers
# ---------------------------------------------------------------------------

def esc(v):
    return html_lib.escape("" if v is None else str(v))


def opt_list(options, selected, empty_label="Any"):
    out = [f'<option value="">{empty_label}</option>']
    for o in options:
        sel = " selected" if o == selected else ""
        out.append(f'<option value="{esc(o)}"{sel}>{esc(o)}</option>')
    return "".join(out)


def status_class(status):
    return "status-" + (status or "").replace(" ", "-").replace("---", "-")


def chip(text, extra_class=""):
    return f'<span class="chip {extra_class}">{esc(text)}</span>'


def tier_badge(tier):
    return f'<span class="tier-badge {esc(tier)}">{esc(TIER_LABEL.get(tier, tier))}</span>'


def tier_at_least(workshop, min_tier):
    if workshop is None:
        return False
    if workshop["status"] != "active":
        return False
    return TIER_RANK.get(workshop["tier"], 0) >= TIER_RANK[min_tier]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def parse_cookies(handler):
    header = handler.headers.get("Cookie")
    cookies = {}
    if header:
        for part in header.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                cookies[k] = v
    return cookies


def get_auth(handler, conn):
    """Returns dict(user=Row, workshop=Row or None) or None if not logged in."""
    token = parse_cookies(handler).get("session")
    if not token:
        return None
    row = conn.execute(
        "SELECT u.*, s.expires_at AS session_expires FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,),
    ).fetchone()
    if row is None:
        return None
    if row["session_expires"] < datetime.now().isoformat():
        return None
    workshop = None
    if row["workshop_id"]:
        workshop = conn.execute("SELECT * FROM workshops WHERE id = ?", (row["workshop_id"],)).fetchone()
    return {"user": row, "workshop": workshop}


def create_session(conn, user_id):
    token = secrets.token_hex(32)
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat()
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (token, user_id, now_str(), expires))
    conn.commit()
    return token


SESSION_COOKIE_TMPL = "session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(title, active, body, flash_msg=None, flash_err=None, auth=None):
    if auth is None:
        nav = f'<a href="/login">Log in</a>&nbsp;&nbsp;<a class="btn small" href="/signup">Start free trial</a>'
        shop_id = f'<div class="shop-id"><strong>{esc(PLATFORM_NAME)}</strong>For workshop owners</div>'
    else:
        user = auth["user"]
        workshop = auth["workshop"]
        if user["role"] == "platform_admin":
            def tab(label, href, key):
                cls = "active" if active == key else ""
                return f'<a href="{href}" class="{cls}">{label}</a>'
            nav = (tab("Admin Dashboard", "/admin", "admin_dashboard")
                   + tab("Workshops", "/admin/workshops", "admin_workshops")
                   + tab("Support Tickets", "/admin/tickets", "admin_tickets")
                   + f'<a href="/logout">Log out</a>')
            shop_id = f'<div class="shop-id"><strong>Platform Admin</strong>{esc(user["name"])}</div>'
        else:
            def tab(label, href, key):
                cls = "active" if active == key else ""
                return f'<a href="{href}" class="{cls}">{label}</a>'
            support_label = "Support" if tier_at_least(workshop, "premium") else "Support 🔒"
            nav = (tab("Tips Library", "/tips", "tips")
                   + tab("Case Log", "/cases", "cases")
                   + tab("Dashboard", "/dashboard", "dashboard")
                   + tab(support_label, "/support", "support")
                   + tab("Account", "/account", "account")
                   + f'<a href="/logout">Log out</a>')
            shop_id = (f'<div class="shop-id"><strong>{esc(workshop["name"] if workshop else "")}</strong>'
                       f'{tier_badge(workshop["tier"] if workshop else "free")}</div>')

    flash_html = ""
    if flash_msg:
        flash_html += f'<div class="flash">{esc(flash_msg)}</div>'
    if flash_err:
        flash_html += f'<div class="flash error">{esc(flash_err)}</div>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - {esc(PLATFORM_NAME)}</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header class="topbar">
  <div class="brand">
    <div class="logo">WN</div>
    <div>
      <h1>{esc(PLATFORM_NAME)}</h1>
      <div class="sub">Shared diagnostics for independent workshops</div>
    </div>
  </div>
  <nav class="tabs">{nav}</nav>
  {shop_id}
</header>
<main>
{flash_html}
{body}
</main>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public pages: landing, signup, login
# ---------------------------------------------------------------------------

def page_landing():
    body = f"""
<div class="hero">
  <h1>{esc(PLATFORM_NAME)}</h1>
  <p>{esc(PLATFORM_TAGLINE)} Every resolved case your network logs makes the whole
  network faster at diagnosing the next one.</p>
  <div class="cta">
    <a class="btn" href="/signup">Start free trial</a>
    <a class="btn secondary" href="/login">Log in</a>
  </div>
</div>
<div class="pricing">
  <div class="price-card">
    <h3>Free</h3>
    <div class="price">{TIER_PRICE['free']}</div>
    <ul>
      <li>Browse the shared Tips library (titles &amp; summaries)</li>
      <li>See which fault codes/models are trending</li>
      <li>Full diagnosis &amp; fix details locked</li>
    </ul>
    <a class="btn secondary" href="/signup" style="width:100%; text-align:center;">Get started</a>
  </div>
  <div class="price-card highlight">
    <h3>Basic</h3>
    <div class="price">{TIER_PRICE['basic']}</div>
    <ul>
      <li>Full access to every Tip's diagnosis &amp; fix</li>
      <li>Private case log for your own technicians</li>
      <li>Promote your resolved cases into shared Tips</li>
      <li>Shop dashboard &amp; CSV export</li>
    </ul>
    <a class="btn" href="/signup" style="width:100%; text-align:center;">Get started</a>
  </div>
  <div class="price-card">
    <h3>Premium</h3>
    <div class="price">{TIER_PRICE['premium']}</div>
    <ul>
      <li>Everything in Basic</li>
      <li>Live in-app technical support - message our team directly on a tricky case</li>
      <li>Priority response</li>
    </ul>
    <a class="btn premium" href="/signup" style="width:100%; text-align:center;">Get started</a>
  </div>
</div>
<p class="small muted" style="text-align:center; max-width:640px; margin:0 auto 40px;">
  Billing is handled directly with our team after signup (bank transfer / invoice) -
  new accounts start on the Free tier and we activate Basic/Premium once payment is
  confirmed. This is an independent workshop tool and is not affiliated with or
  endorsed by Mercedes-Benz.
</p>"""
    return layout("Home", "", body)


def page_signup(err=None, form=None):
    form = form or {}
    body = f"""
<div class="auth-wrap">
  <div class="panel">
    <h2>Start your free trial</h2>
    <p class="muted small">Create your workshop account. You can browse the shared Tips
    library immediately - upgrade any time for full access and case logging.</p>
    <form class="stack" method="post" action="/signup">
      <div class="field"><label>Workshop name</label>
        <input type="text" name="workshop_name" required value="{esc(form.get('workshop_name',''))}"></div>
      <div class="field"><label>City</label>
        <input type="text" name="city" value="{esc(form.get('city',''))}"></div>
      <div class="field"><label>Phone <span class="hint">optional</span></label>
        <input type="text" name="phone" value="{esc(form.get('phone',''))}"></div>
      <div class="field"><label>Your name</label>
        <input type="text" name="owner_name" required value="{esc(form.get('owner_name',''))}"></div>
      <div class="field"><label>Email (this is your login)</label>
        <input type="email" name="email" required value="{esc(form.get('email',''))}"></div>
      <div class="field"><label>Password</label>
        <input type="password" name="password" required minlength="6"></div>
      <button class="btn" type="submit" style="width:100%;">Create account</button>
    </form>
    <p class="small muted" style="margin-top:14px;">Already have an account? <a href="/login">Log in</a></p>
  </div>
</div>"""
    return layout("Sign up", "", body, flash_err=err)


def page_login(err=None, email=""):
    body = f"""
<div class="auth-wrap">
  <div class="panel">
    <h2>Log in</h2>
    <form class="stack" method="post" action="/login">
      <div class="field"><label>Email</label><input type="email" name="email" required value="{esc(email)}"></div>
      <div class="field"><label>Password</label><input type="password" name="password" required></div>
      <button class="btn" type="submit" style="width:100%;">Log in</button>
    </form>
    <p class="small muted" style="margin-top:14px;">New workshop? <a href="/signup">Start a free trial</a></p>
  </div>
</div>"""
    return layout("Log in", "", body, flash_err=err)


# ---------------------------------------------------------------------------
# Workshop pages: Tips
# ---------------------------------------------------------------------------

def page_tips_list(qs, auth, flash_msg=None):
    conn = get_conn()
    workshop = auth["workshop"]
    full_access = tier_at_least(workshop, "basic")

    q = (qs.get("q", [""])[0]).strip()
    category = (qs.get("category", [""])[0]).strip()
    function_group = (qs.get("function_group", [""])[0]).strip()
    model = (qs.get("model", [""])[0]).strip()
    fault_code = (qs.get("fault_code", [""])[0]).strip()

    sql = "SELECT * FROM tips WHERE 1=1"
    params = []
    if q:
        sql += " AND (title LIKE ? OR symptom LIKE ? OR diagnosis LIKE ? OR fix LIKE ? OR topic_number LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like, like, like]
    if category:
        sql += " AND category = ?"; params.append(category)
    if function_group:
        sql += " AND function_group = ?"; params.append(function_group)
    if model:
        sql += " AND model_series LIKE ?"; params.append(f"%{model}%")
    if fault_code:
        sql += " AND fault_codes LIKE ?"; params.append(f"%{fault_code}%")
    sql += " ORDER BY created_at DESC"

    tips = conn.execute(sql, params).fetchall()
    total = conn.execute("SELECT COUNT(*) AS n FROM tips").fetchone()["n"]
    conn.close()

    rows = ""
    for t in tips:
        confirmed = f'<span class="chip">x{t["confirm_count"]} cases</span>' if t["confirm_count"] > 0 else '<span class="muted small">new</span>'
        rows += f"""<tr onclick="window.location='/tips/{t['id']}'" style="cursor:pointer;">
          <td>{esc(t['topic_number'])}</td>
          <td><a href="/tips/{t['id']}">{esc(t['title'])}</a></td>
          <td>{esc(t['model_series'] or '-')}</td>
          <td>{confirmed}</td>
          <td class="muted small">{esc(t['created_at'])}</td>
        </tr>"""

    if tips:
        table = f"""<table class="list">
        <thead><tr><th>Topic #</th><th>Title</th><th>Model series</th><th>Confirmed in field</th><th>Date</th></tr></thead>
        <tbody>{rows}</tbody></table>"""
    else:
        table = '<div class="panel empty">No tips match these filters yet.</div>'

    upsell = ""
    if not full_access:
        upsell = """<div class="lock-overlay" style="margin-bottom:16px;">
          <h3>You're on the Free tier</h3>
          <p>You can browse every Tip's title here. Upgrade to Basic to open full diagnosis
          and fix details, and to start logging your own private cases.</p>
          <a class="btn" href="/account">See upgrade options</a>
        </div>"""

    body = f"""
<div class="page-head">
  <h2>Tips Library <span class="count">{total} total entries, contributed by the network</span></h2>
  <div class="top-actions">
    {"<a class='btn secondary' href='/tips/export.csv'>Export CSV</a>" if full_access else ""}
    {"<a class='btn' href='/tips/new'>+ Add Tip</a>" if full_access else ""}
  </div>
</div>
{upsell}
<div class="layout">
  <div class="panel filters">
    <form method="get" action="/tips">
      <div class="field"><label>Free text search</label>
        <input type="text" name="q" value="{esc(q)}" placeholder="title, symptom, fix..."></div>
      <div class="field"><label>Symptom category</label>
        <select name="category">{opt_list(SYMPTOM_CATEGORIES, category)}</select></div>
      <div class="field"><label>Function group</label>
        <select name="function_group">{opt_list(FUNCTION_GROUPS, function_group)}</select></div>
      <div class="field"><label>Model series</label>
        <input type="text" name="model" value="{esc(model)}" placeholder="e.g. W213, 223"></div>
      <div class="field"><label>Fault code</label>
        <input type="text" name="fault_code" value="{esc(fault_code)}" placeholder="e.g. B00292B"></div>
      <button class="btn" type="submit">Search</button>
      <a class="btn secondary" style="width:100%; text-align:center; margin-top:8px; display:block;" href="/tips">Reset filters</a>
    </form>
  </div>
  <div>{table}</div>
</div>"""
    return layout("Tips Library", "tips", body, flash_msg, auth=auth)


def page_tip_detail(tip_id, auth, flash_msg=None):
    conn = get_conn()
    workshop = auth["workshop"]
    full_access = tier_at_least(workshop, "basic")
    tip = conn.execute("SELECT * FROM tips WHERE id = ?", (tip_id,)).fetchone()
    if tip is None:
        conn.close()
        return None

    linked_cases = []
    if full_access:
        linked_cases = conn.execute(
            "SELECT * FROM cases WHERE linked_tip_id = ? AND workshop_id = ? ORDER BY created_at DESC",
            (tip_id, workshop["id"]),
        ).fetchall()
    conn.close()

    meta = [chip(tip["topic_number"])]
    if tip["model_series"]: meta.append(chip(tip["model_series"]))
    if tip["function_group"]: meta.append(chip(tip["function_group"]))
    if tip["control_unit"]: meta.append(chip(tip["control_unit"]))
    if tip["fault_codes"]: meta.append(chip("Codes: " + tip["fault_codes"]))
    meta.append(chip(f"Confirmed in {tip['confirm_count']} case(s) network-wide"))

    if full_access:
        detail_sections = f"""
        <div class="section"><h4>Symptom</h4><p>{esc(tip['symptom'] or '-')}</p></div>
        <div class="section"><h4>Diagnosis / how it was confirmed</h4><p>{esc(tip['diagnosis'] or '-')}</p></div>
        <div class="section"><h4>Fix</h4><p>{esc(tip['fix'] or '-')}</p></div>
        {"<div class='section'><h4>Notes</h4><p>" + esc(tip['notes']) + "</p></div>" if tip['notes'] else ""}
        """
    else:
        detail_sections = f"""
        <div class="section"><h4>Symptom</h4><p>{esc(tip['symptom'] or '-')}</p></div>
        <div class="lock-overlay">
          <h3>Diagnosis &amp; fix are on Basic and above</h3>
          <p>Upgrade to see exactly how this was diagnosed and fixed, plus how many
          other workshops confirmed the same fix.</p>
          <a class="btn" href="/account">See upgrade options</a>
        </div>
        """

    linked_html = ""
    if full_access and linked_cases:
        lrows = "".join(f"""<tr onclick="window.location='/cases/{c['id']}'" style="cursor:pointer;">
          <td>{esc(c['case_number'])}</td><td>{esc(c['technician'])}</td><td>{esc(c['vehicle_model'] or '-')}</td>
          <td><span class="chip {status_class(c['status'])}">{esc(c['status'])}</span></td>
          <td class="muted small">{esc(c['case_date'])}</td></tr>""" for c in linked_cases)
        linked_html = f"""<h3 style="margin-top:28px;">Your cases linked to this tip ({len(linked_cases)})</h3>
        <table class="list"><thead><tr><th>Case #</th><th>Technician</th><th>Vehicle</th><th>Status</th><th>Date</th></tr></thead>
        <tbody>{lrows}</tbody></table>"""

    body = f"""
<p><a href="/tips">&larr; Back to Tips Library</a></p>
<div class="detail-panel">
  <h2>{esc(tip['title'])}</h2>
  <div class="meta-row">{''.join(meta)}</div>
  {detail_sections}
  <div class="section small muted">Source: {esc(tip['source'])} (contributing workshop kept private) &middot; Added {esc(tip['created_at'])}</div>
</div>
{linked_html}"""
    return layout(tip["title"], "tips", body, flash_msg, auth=auth)


def page_tip_form(auth):
    body = f"""
<p><a href="/tips">&larr; Back to Tips Library</a></p>
<div class="page-head"><h2>Add a Tip to the Shared Library</h2></div>
<form class="stack panel" method="post" action="/tips/new" style="max-width:760px;">
  <div class="field"><label>Title</label>
    <input type="text" name="title" required placeholder="e.g. Front axle creak over speed bumps"></div>
  <div class="grid2">
    <div class="field"><label>Symptom category</label><select name="category">{opt_list(SYMPTOM_CATEGORIES, "", "-")}</select></div>
    <div class="field"><label>Function group</label><select name="function_group">{opt_list(FUNCTION_GROUPS, "", "-")}</select></div>
  </div>
  <div class="grid2">
    <div class="field"><label>Control unit <span class="hint">optional</span></label><input type="text" name="control_unit" placeholder="e.g. EPS223"></div>
    <div class="field"><label>Fault code(s) <span class="hint">comma separated</span></label><input type="text" name="fault_codes" placeholder="e.g. B00292B"></div>
  </div>
  <div class="field"><label>Model series</label><input type="text" name="model_series" placeholder="e.g. W213, 223"></div>
  <div class="field"><label>Symptom</label><textarea name="symptom"></textarea></div>
  <div class="field"><label>Diagnosis</label><textarea name="diagnosis"></textarea></div>
  <div class="field"><label>Fix</label><textarea name="fix"></textarea></div>
  <div class="field"><label>Notes <span class="hint">optional</span></label><textarea name="notes"></textarea></div>
  <p class="small muted">This will be visible to every workshop on the network. Your
  workshop name is never shown to other subscribers.</p>
  <button class="btn" type="submit">Save Tip</button>
</form>"""
    return layout("Add Tip", "tips", body, auth=auth)


# ---------------------------------------------------------------------------
# Workshop pages: Cases (private per workshop)
# ---------------------------------------------------------------------------

def page_cases_list(qs, auth, flash_msg=None):
    conn = get_conn()
    workshop_id = auth["workshop"]["id"]
    status = (qs.get("status", [""])[0]).strip()
    technician = (qs.get("technician", [""])[0]).strip()
    q = (qs.get("q", [""])[0]).strip()

    sql = "SELECT * FROM cases WHERE workshop_id = ?"
    params = [workshop_id]
    if status:
        sql += " AND status = ?"; params.append(status)
    if technician:
        sql += " AND technician LIKE ?"; params.append(f"%{technician}%")
    if q:
        sql += " AND (symptom LIKE ? OR fault_codes LIKE ? OR vehicle_model LIKE ? OR case_number LIKE ?)"
        like = f"%{q}%"; params += [like, like, like, like]
    sql += " ORDER BY created_at DESC"

    cases = conn.execute(sql, params).fetchall()
    open_count = conn.execute(
        "SELECT COUNT(*) AS n FROM cases WHERE workshop_id = ? AND status IN ('Open','In Progress')",
        (workshop_id,),
    ).fetchone()["n"]
    conn.close()

    rows = ""
    for c in cases:
        symptom_short = (c["symptom"] or "")[:60] + ("..." if len(c["symptom"] or "") > 60 else "")
        rows += f"""<tr onclick="window.location='/cases/{c['id']}'" style="cursor:pointer;">
          <td>{esc(c['case_number'])}</td><td>{esc(c['vehicle_model'] or '-')}</td>
          <td>{esc(symptom_short)}</td><td>{esc(c['technician'])}</td>
          <td><span class="chip {status_class(c['status'])}">{esc(c['status'])}</span></td>
          <td class="muted small">{esc(c['case_date'])}</td></tr>"""

    if cases:
        table = f"""<table class="list"><thead><tr><th>Case #</th><th>Vehicle</th><th>Symptom</th><th>Technician</th><th>Status</th><th>Date</th></tr></thead>
        <tbody>{rows}</tbody></table>"""
    else:
        table = '<div class="panel empty">No cases logged yet. Click "+ Log New Case" to add the first one.<br>Only your workshop can see these.</div>'

    body = f"""
<div class="page-head">
  <h2>Case Log <span class="count">private to your workshop &middot; {open_count} open / in progress</span></h2>
  <div class="top-actions">
    <a class="btn secondary" href="/cases/export.csv">Export CSV</a>
    <a class="btn" href="/cases/new">+ Log New Case</a>
  </div>
</div>
<div class="layout">
  <div class="panel filters">
    <form method="get" action="/cases">
      <div class="field"><label>Free text search</label><input type="text" name="q" value="{esc(q)}" placeholder="symptom, code, model..."></div>
      <div class="field"><label>Status</label><select name="status">{opt_list(CASE_STATUSES, status)}</select></div>
      <div class="field"><label>Technician</label><input type="text" name="technician" value="{esc(technician)}"></div>
      <button class="btn" type="submit">Search</button>
      <a class="btn secondary" style="width:100%; text-align:center; margin-top:8px; display:block;" href="/cases">Reset filters</a>
    </form>
  </div>
  <div>{table}</div>
</div>"""
    return layout("Case Log", "cases", body, flash_msg, auth=auth)


def page_case_form(auth):
    body = f"""
<p><a href="/cases">&larr; Back to Case Log</a></p>
<div class="page-head"><h2>Log New Case</h2></div>
<form class="stack panel" method="post" action="/cases/new" style="max-width:820px;">
  <div class="grid3">
    <div class="field"><label>Technician</label><input type="text" name="technician" required placeholder="Your name"></div>
    <div class="field"><label>Date</label><input type="date" name="case_date"></div>
    <div class="field"><label>Status</label><select name="status">{"".join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in CASE_STATUSES)}</select></div>
  </div>
  <div class="grid3">
    <div class="field"><label>Vehicle model</label><input type="text" name="vehicle_model" placeholder="e.g. S 580 e Sedan (223)"></div>
    <div class="field"><label>VIN <span class="hint">optional</span></label><input type="text" name="vin"></div>
    <div class="field"><label>Mileage <span class="hint">optional</span></label><input type="text" name="mileage"></div>
  </div>
  <div class="grid2">
    <div class="field"><label>Function group</label><select name="function_group">{opt_list(FUNCTION_GROUPS, "", "-")}</select></div>
    <div class="field"><label>Control unit <span class="hint">optional</span></label><input type="text" name="control_unit"></div>
  </div>
  <div class="field"><label>Fault code(s) <span class="hint">comma separated, if any</span></label><input type="text" name="fault_codes" placeholder="e.g. B00292B"></div>
  <div class="field"><label>Symptom / customer complaint</label><textarea name="symptom" required></textarea></div>
  <div class="field"><label>Diagnosis steps</label><textarea name="diagnosis_steps"></textarea></div>
  <div class="field"><label>Root cause <span class="hint">fill in once known</span></label><textarea name="root_cause"></textarea></div>
  <div class="field"><label>Fix applied</label><textarea name="fix_applied"></textarea></div>
  <div class="grid2">
    <div class="field"><label>Parts used <span class="hint">optional</span></label><input type="text" name="parts_used"></div>
    <div class="field"><label>Time spent (hours) <span class="hint">optional</span></label><input type="number" step="0.25" name="time_spent_hours"></div>
  </div>
  <div class="field"><label>Notes <span class="hint">optional</span></label><textarea name="notes"></textarea></div>
  <button class="btn" type="submit">Save Case</button>
</form>"""
    return layout("Log New Case", "cases", body, auth=auth)


def page_case_detail(case_id, auth, flash_msg=None):
    conn = get_conn()
    workshop_id = auth["workshop"]["id"]
    case = conn.execute("SELECT * FROM cases WHERE id = ? AND workshop_id = ?", (case_id, workshop_id)).fetchone()
    if case is None:
        conn.close()
        return None
    linked_tip = None
    if case["linked_tip_id"]:
        linked_tip = conn.execute("SELECT * FROM tips WHERE id = ?", (case["linked_tip_id"],)).fetchone()
    related = []
    if case["fault_codes"]:
        first_code = case["fault_codes"].split(",")[0].strip()
        if first_code:
            related = conn.execute("SELECT * FROM tips WHERE fault_codes LIKE ? LIMIT 5", (f"%{first_code}%",)).fetchall()
    conn.close()

    meta = [chip(case["vehicle_model"] or "No model given")]
    if case["vin"]: meta.append(chip("VIN " + case["vin"]))
    if case["fault_codes"]: meta.append(chip("Codes: " + case["fault_codes"]))
    if case["function_group"]: meta.append(chip(case["function_group"]))
    meta.append(chip(case["technician"]))
    meta.append(chip(case["case_date"]))
    if case["time_spent_hours"]: meta.append(chip(f"{case['time_spent_hours']} hrs"))

    extra_sections = ""
    if case["parts_used"]:
        extra_sections += f"<div class='section'><h4>Parts used</h4><p>{esc(case['parts_used'])}</p></div>"
    if case["notes"]:
        extra_sections += f"<div class='section'><h4>Notes</h4><p>{esc(case['notes'])}</p></div>"

    linked_tip_html = ""
    if linked_tip:
        linked_tip_html = f"""<div class="section"><h4>Linked shared tip</h4>
        <p><a href="/tips/{linked_tip['id']}">{esc(linked_tip['topic_number'])} - {esc(linked_tip['title'])}</a></p></div>"""

    promote_html = ""
    if not linked_tip:
        related_html = ""
        if related:
            rel_items = ""
            for r in related:
                rel_items += f"""<form method="post" action="/cases/{case_id}/link" style="display:flex; align-items:center; gap:10px; margin-bottom:6px;">
                  <input type="hidden" name="tip_id" value="{r['id']}">
                  <span class="small">{esc(r['topic_number'])} - {esc(r['title'])}</span>
                  <button class="btn small secondary" type="submit">Link &amp; confirm</button>
                </form>"""
            related_html = f"""<div style="margin-top:18px;">
              <p class="small muted" style="margin-bottom:6px;">Or, this looks similar to an existing shared tip - link it instead and add a confirmation:</p>
              {rel_items}</div>"""
        default_title = esc((case["symptom"] or "")[:80])
        promote_html = f"""<div class="panel" style="margin-top:20px;">
  <h3 style="margin-top:0;">Share this as a Tip</h3>
  <p class="muted small">If this case is resolved and the fix could help other workshops,
  promote it into the shared Tips library. Your workshop's name stays private.</p>
  <form method="post" action="/cases/{case_id}/promote" style="display:flex; gap:10px; align-items:flex-end; flex-wrap:wrap;">
    <div style="flex:1; min-width:260px;">
      <label class="small muted" style="display:block; margin-bottom:4px;">Tip title</label>
      <input type="text" name="title" style="width:100%; padding:8px 10px; border:1px solid var(--border); border-radius:6px;" value="{default_title}">
    </div>
    <button class="btn" type="submit">Promote to shared Tip</button>
  </form>
  {related_html}
</div>"""

    body = f"""
<p><a href="/cases">&larr; Back to Case Log</a></p>
<div class="detail-panel">
  <h2>{esc(case['case_number'])} <span class="chip {status_class(case['status'])}">{esc(case['status'])}</span></h2>
  <div class="meta-row">{''.join(meta)}</div>
  <div class="section"><h4>Symptom</h4><p>{esc(case['symptom'])}</p></div>
  <div class="section"><h4>Diagnosis steps</h4><p>{esc(case['diagnosis_steps'] or '-')}</p></div>
  <div class="section"><h4>Root cause</h4><p>{esc(case['root_cause'] or '-')}</p></div>
  <div class="section"><h4>Fix applied</h4><p>{esc(case['fix_applied'] or '-')}</p></div>
  {extra_sections}
  {linked_tip_html}
</div>
{promote_html}"""
    return layout(case["case_number"], "cases", body, flash_msg, auth=auth)


# ---------------------------------------------------------------------------
# Workshop pages: Dashboard, Account
# ---------------------------------------------------------------------------

def bar_rows(rows, key):
    if not rows:
        return '<p class="muted small">No data yet.</p>'
    max_n = rows[0]["n"] or 1
    out = ""
    for r in rows:
        pct = round((r["n"] / max_n) * 100)
        out += f"""<div class="bar-row"><div class="name">{esc(r[key])}</div>
        <div class="bar-track"><div class="bar-fill" style="width:{pct}%;"></div></div>
        <div class="bar-n">{r['n']}</div></div>"""
    return out


def page_dashboard(auth):
    conn = get_conn()
    workshop_id = auth["workshop"]["id"]
    total_cases = conn.execute("SELECT COUNT(*) AS n FROM cases WHERE workshop_id=?", (workshop_id,)).fetchone()["n"]
    open_cases = conn.execute("SELECT COUNT(*) AS n FROM cases WHERE workshop_id=? AND status IN ('Open','In Progress')", (workshop_id,)).fetchone()["n"]
    resolved = conn.execute("SELECT COUNT(*) AS n FROM cases WHERE workshop_id=? AND status='Resolved'", (workshop_id,)).fetchone()["n"]
    resolution_rate = round((resolved / total_cases) * 100, 1) if total_cases else 0
    network_tips = conn.execute("SELECT COUNT(*) AS n FROM tips").fetchone()["n"]

    top_fault_codes = conn.execute(
        "SELECT fault_codes, COUNT(*) AS n FROM cases WHERE workshop_id=? AND fault_codes IS NOT NULL AND fault_codes!='' GROUP BY fault_codes ORDER BY n DESC LIMIT 8",
        (workshop_id,),
    ).fetchall()
    top_models = conn.execute(
        "SELECT vehicle_model, COUNT(*) AS n FROM cases WHERE workshop_id=? AND vehicle_model IS NOT NULL AND vehicle_model!='' GROUP BY vehicle_model ORDER BY n DESC LIMIT 8",
        (workshop_id,),
    ).fetchall()
    top_technicians = conn.execute(
        "SELECT technician, COUNT(*) AS n FROM cases WHERE workshop_id=? GROUP BY technician ORDER BY n DESC LIMIT 8",
        (workshop_id,),
    ).fetchall()
    recent_cases = conn.execute("SELECT * FROM cases WHERE workshop_id=? ORDER BY created_at DESC LIMIT 6", (workshop_id,)).fetchall()
    conn.close()

    recent_html = ""
    if recent_cases:
        for c in recent_cases:
            s = (c["symptom"] or "")[:45] + ("..." if len(c["symptom"] or "") > 45 else "")
            recent_html += f"""<div class="bar-row" style="cursor:pointer;" onclick="window.location='/cases/{c['id']}'">
              <div class="name" style="width:90px;">{esc(c['case_number'])}</div>
              <div style="flex:1; font-size:13px;">{esc(s)}</div>
              <span class="chip {status_class(c['status'])}">{esc(c['status'])}</span></div>"""
    else:
        recent_html = '<p class="muted small">No cases logged yet.</p>'

    body = f"""
<div class="page-head"><h2>Your Dashboard</h2></div>
<div class="cards">
  <div class="card"><div class="num">{total_cases}</div><div class="label">Cases logged (your workshop)</div></div>
  <div class="card"><div class="num">{open_cases}</div><div class="label">Open / in progress</div></div>
  <div class="card"><div class="num">{resolution_rate}%</div><div class="label">Your resolution rate</div></div>
  <div class="card"><div class="num">{network_tips}</div><div class="label">Tips in the shared network library</div></div>
</div>
<div class="two-col">
  <div class="panel"><h3 style="margin-top:0;">Your most common fault codes</h3>{bar_rows(top_fault_codes, 'fault_codes')}</div>
  <div class="panel"><h3 style="margin-top:0;">Your most common vehicle models</h3>{bar_rows(top_models, 'vehicle_model')}</div>
</div>
<div class="two-col" style="margin-top:18px;">
  <div class="panel"><h3 style="margin-top:0;">Cases by technician</h3>{bar_rows(top_technicians, 'technician')}</div>
  <div class="panel"><h3 style="margin-top:0;">Recent cases</h3>{recent_html}</div>
</div>"""
    return layout("Dashboard", "dashboard", body, auth=auth)


def page_account(auth, flash_msg=None):
    workshop = auth["workshop"]
    tier = workshop["tier"]
    status = workshop["status"]
    rows_html = ""
    for t in ["free", "basic", "premium"]:
        current = " &larr; current plan" if t == tier else ""
        rows_html += f"<li>{tier_badge(t)} {TIER_PRICE[t]}{current}</li>"

    body = f"""
<div class="page-head"><h2>Account</h2></div>
<div class="detail-panel">
  <div class="meta-row">
    {tier_badge(tier)}
    <span class="chip status-{esc(status)}">{esc(status).title()}</span>
  </div>
  <div class="section"><h4>Workshop</h4><p>{esc(workshop['name'])}{(' - ' + esc(workshop['city'])) if workshop['city'] else ''}</p></div>
  <div class="section"><h4>Contact email</h4><p>{esc(workshop['contact_email'] or '-')}</p></div>
  <div class="section"><h4>Plans</h4><ul style="margin:0; padding-left:18px; line-height:1.9;">{rows_html}</ul></div>
  <div class="section">
    <h4>Upgrade or change your plan</h4>
    <p>Billing is handled directly with our team - reply to your onboarding email or
    contact us to upgrade/downgrade. Once payment is confirmed we'll activate the new
    tier on your account within one business day.</p>
  </div>
</div>"""
    return layout("Account", "account", body, flash_msg, auth=auth)


# ---------------------------------------------------------------------------
# Workshop pages: Support (Premium only)
# ---------------------------------------------------------------------------

def page_support_locked(auth):
    body = """
<div class="page-head"><h2>Live Technical Support</h2></div>
<div class="lock-overlay">
  <h3>This is a Premium feature</h3>
  <p>Premium workshops can message our technical team directly in-app about a specific
  case - fault codes, wiring diagrams, unusual symptoms, whatever you're stuck on.</p>
  <a class="btn premium" href="/account">See upgrade options</a>
</div>"""
    return layout("Support", "support", body, auth=auth)


def page_support_list(auth, flash_msg=None):
    conn = get_conn()
    workshop_id = auth["workshop"]["id"]
    tickets = conn.execute(
        "SELECT * FROM tickets WHERE workshop_id = ? ORDER BY updated_at DESC", (workshop_id,)
    ).fetchall()
    conn.close()

    rows = ""
    if tickets:
        for t in tickets:
            rows += f"""<div class="ticket-row" style="cursor:pointer;" onclick="window.location='/support/{t['id']}'">
              <div><strong>{esc(t['subject'])}</strong><div class="muted small">{esc(t['vehicle_model'] or '')}</div></div>
              <div><span class="chip {status_class(t['status'])}">{esc(t['status']).title()}</span> <span class="muted small">{esc(t['updated_at'])}</span></div>
            </div>"""
        list_html = f'<div class="panel">{rows}</div>'
    else:
        list_html = '<div class="panel empty">No support tickets yet. Open one below if you need help with a case.</div>'

    body = f"""
<div class="page-head"><h2>Live Technical Support</h2></div>
<div class="panel" style="margin-bottom:18px;">
  <h3 style="margin-top:0;">Open a new ticket</h3>
  <form class="stack" method="post" action="/support/new">
    <div class="field"><label>Subject</label><input type="text" name="subject" required placeholder="e.g. Intermittent P0AFA fault on 2021 EQC"></div>
    <div class="field"><label>Vehicle model <span class="hint">optional</span></label><input type="text" name="vehicle_model"></div>
    <div class="field"><label>Message</label><textarea name="message" required placeholder="Describe what you're stuck on..."></textarea></div>
    <button class="btn premium" type="submit">Send to support team</button>
  </form>
</div>
<h3>Your tickets</h3>
{list_html}"""
    return layout("Support", "support", body, flash_msg, auth=auth)


def page_support_thread(ticket_id, auth, flash_msg=None):
    conn = get_conn()
    workshop_id = auth["workshop"]["id"]
    ticket = conn.execute("SELECT * FROM tickets WHERE id=? AND workshop_id=?", (ticket_id, workshop_id)).fetchone()
    if ticket is None:
        conn.close()
        return None
    messages = conn.execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY created_at ASC", (ticket_id,)).fetchall()
    conn.close()

    msg_html = ""
    for m in messages:
        who = "You" if m["sender_role"] == "workshop" else "Support team"
        msg_html += f'<div class="msg {esc(m["sender_role"])}"><span class="who">{esc(who)} &middot; {esc(m["created_at"])}</span>{esc(m["message"])}</div>'

    reply_html = ""
    if ticket["status"] != "closed":
        reply_html = f"""<form class="stack panel" method="post" action="/support/{ticket_id}/reply">
          <div class="field"><textarea name="message" required placeholder="Type a reply..."></textarea></div>
          <button class="btn premium" type="submit">Send</button>
        </form>"""
    else:
        reply_html = '<p class="muted small">This ticket is closed.</p>'

    body = f"""
<p><a href="/support">&larr; Back to Support</a></p>
<div class="detail-panel">
  <h2>{esc(ticket['subject'])} <span class="chip {status_class(ticket['status'])}">{esc(ticket['status']).title()}</span></h2>
  <div class="meta-row">{chip(ticket['vehicle_model']) if ticket['vehicle_model'] else ''}</div>
  <div class="thread">{msg_html}</div>
</div>
<div style="margin-top:16px;">{reply_html}</div>"""
    return layout(ticket["subject"], "support", body, flash_msg, auth=auth)


# ---------------------------------------------------------------------------
# Admin pages
# ---------------------------------------------------------------------------

def page_admin_dashboard(auth):
    conn = get_conn()
    total_workshops = conn.execute("SELECT COUNT(*) AS n FROM workshops").fetchone()["n"]
    by_tier = conn.execute("SELECT tier, COUNT(*) AS n FROM workshops GROUP BY tier").fetchall()
    total_cases = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
    total_tips = conn.execute("SELECT COUNT(*) AS n FROM tips").fetchone()["n"]
    open_tickets = conn.execute("SELECT COUNT(*) AS n FROM tickets WHERE status='open'").fetchone()["n"]
    recent_workshops = conn.execute("SELECT * FROM workshops ORDER BY created_at DESC LIMIT 6").fetchall()
    conn.close()

    tier_counts = {r["tier"]: r["n"] for r in by_tier}
    tier_html = "".join(f"<div class='bar-row'><div class='name'>{tier_badge(t)}</div><div class='bar-n' style='width:auto;'>{tier_counts.get(t,0)}</div></div>" for t in ["free","basic","premium"])

    rw_html = ""
    for w in recent_workshops:
        rw_html += f"""<div class="bar-row" style="cursor:pointer;" onclick="window.location='/admin/workshops'">
          <div class="name" style="width:220px;">{esc(w['name'])}</div>
          <div>{tier_badge(w['tier'])} <span class="chip status-{esc(w['status'])}">{esc(w['status']).title()}</span></div>
        </div>"""

    body = f"""
<div class="page-head"><h2>Admin Dashboard</h2></div>
<div class="cards">
  <div class="card"><div class="num">{total_workshops}</div><div class="label">Workshops on the network</div></div>
  <div class="card"><div class="num">{total_cases}</div><div class="label">Total cases logged (all workshops)</div></div>
  <div class="card"><div class="num">{total_tips}</div><div class="label">Shared tips</div></div>
  <div class="card"><div class="num">{open_tickets}</div><div class="label">Open support tickets</div></div>
</div>
<div class="two-col">
  <div class="panel"><h3 style="margin-top:0;">Workshops by tier</h3>{tier_html}</div>
  <div class="panel"><h3 style="margin-top:0;">Recently signed up</h3>{rw_html or '<p class="muted small">None yet.</p>'}</div>
</div>"""
    return layout("Admin Dashboard", "admin_dashboard", body, auth=auth)


def page_admin_workshops(auth, flash_msg=None):
    conn = get_conn()
    workshops = conn.execute("SELECT * FROM workshops ORDER BY created_at DESC").fetchall()
    conn.close()

    rows = ""
    for w in workshops:
        tier_opts = "".join(f'<option value="{t}" {"selected" if w["tier"]==t else ""}>{TIER_LABEL[t]}</option>' for t in ["free","basic","premium"])
        status_opts = "".join(f'<option value="{s}" {"selected" if w["status"]==s else ""}>{s.title()}</option>' for s in WORKSHOP_STATUSES)
        rows += f"""<tr>
          <td>{esc(w['name'])}<div class="muted small">{esc(w['city'] or '')}</div></td>
          <td class="small">{esc(w['contact_email'] or '-')}</td>
          <td class="muted small">{esc(w['created_at'])}</td>
          <td>
            <form method="post" action="/admin/workshops/{w['id']}/update" style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
              <select name="tier">{tier_opts}</select>
              <select name="status">{status_opts}</select>
              <button class="btn small" type="submit">Save</button>
            </form>
          </td>
        </tr>"""

    body = f"""
<div class="page-head"><h2>Workshops <span class="count">{len(workshops)} total</span></h2></div>
<table class="list">
  <thead><tr><th>Workshop</th><th>Contact</th><th>Signed up</th><th>Plan / status</th></tr></thead>
  <tbody>{rows}</tbody>
</table>"""
    return layout("Workshops", "admin_workshops", body, flash_msg, auth=auth)


def page_admin_tickets(qs, auth):
    conn = get_conn()
    status = (qs.get("status", [""])[0]).strip()
    sql = """SELECT t.*, w.name AS workshop_name FROM tickets t
             JOIN workshops w ON w.id = t.workshop_id WHERE 1=1"""
    params = []
    if status:
        sql += " AND t.status = ?"; params.append(status)
    sql += " ORDER BY t.updated_at DESC"
    tickets = conn.execute(sql, params).fetchall()
    conn.close()

    rows = ""
    for t in tickets:
        rows += f"""<div class="ticket-row" style="cursor:pointer;" onclick="window.location='/admin/tickets/{t['id']}'">
          <div><strong>{esc(t['subject'])}</strong><div class="muted small">{esc(t['workshop_name'])} &middot; {esc(t['vehicle_model'] or '')}</div></div>
          <div><span class="chip {status_class(t['status'])}">{esc(t['status']).title()}</span> <span class="muted small">{esc(t['updated_at'])}</span></div>
        </div>"""
    list_html = f'<div class="panel">{rows}</div>' if tickets else '<div class="panel empty">No tickets.</div>'

    body = f"""
<div class="page-head"><h2>Support Tickets</h2></div>
<div class="filters panel" style="margin-bottom:16px;">
  <form method="get" action="/admin/tickets" style="display:flex; gap:10px; align-items:end;">
    <div class="field" style="margin:0;"><label>Status</label><select name="status">{opt_list(["open","answered","closed"], status)}</select></div>
    <button class="btn small" type="submit">Filter</button>
  </form>
</div>
{list_html}"""
    return layout("Support Tickets", "admin_tickets", body, auth=auth)


def page_admin_ticket_thread(ticket_id, auth, flash_msg=None):
    conn = get_conn()
    ticket = conn.execute(
        "SELECT t.*, w.name AS workshop_name FROM tickets t JOIN workshops w ON w.id=t.workshop_id WHERE t.id=?",
        (ticket_id,),
    ).fetchone()
    if ticket is None:
        conn.close()
        return None
    messages = conn.execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY created_at ASC", (ticket_id,)).fetchall()
    conn.close()

    msg_html = ""
    for m in messages:
        who = ticket["workshop_name"] if m["sender_role"] == "workshop" else "You (support team)"
        msg_html += f'<div class="msg {esc(m["sender_role"])}"><span class="who">{esc(who)} &middot; {esc(m["created_at"])}</span>{esc(m["message"])}</div>'

    close_btn = ""
    if ticket["status"] != "closed":
        close_btn = f"""<form method="post" action="/admin/tickets/{ticket_id}/close" style="display:inline;">
          <button class="btn small secondary" type="submit">Mark closed</button></form>"""

    reply_html = ""
    if ticket["status"] != "closed":
        reply_html = f"""<form class="stack panel" method="post" action="/admin/tickets/{ticket_id}/reply">
          <div class="field"><textarea name="message" required placeholder="Type a reply..."></textarea></div>
          <button class="btn" type="submit">Send reply</button>
        </form>"""

    body = f"""
<p><a href="/admin/tickets">&larr; Back to Support Tickets</a></p>
<div class="detail-panel">
  <h2>{esc(ticket['subject'])} <span class="chip {status_class(ticket['status'])}">{esc(ticket['status']).title()}</span></h2>
  <div class="meta-row">{chip(ticket['workshop_name'])} {chip(ticket['vehicle_model']) if ticket['vehicle_model'] else ''} {close_btn}</div>
  <div class="thread">{msg_html}</div>
</div>
<div style="margin-top:16px;">{reply_html}</div>"""
    return layout(ticket["subject"], "admin_tickets", body, flash_msg, auth=auth)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def csv_bytes(rows):
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
    return output.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP server / routing
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "WorkshopNetwork/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, content, content_type="text/html; charset=utf-8", extra_headers=None):
        body = content.encode("utf-8") if isinstance(content, str) else content
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location, set_cookie=None, clear_cookie=False):
        self.send_response(303)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header("Set-Cookie", SESSION_COOKIE_TMPL.format(token=set_cookie, max_age=SESSION_DAYS * 86400))
        if clear_cookie:
            self.send_header("Set-Cookie", "session=; Path=/; HttpOnly; Max-Age=0")
        self.end_headers()

    def _not_found(self, auth=None):
        self._send(404, layout("Not found", "", '<div class="panel empty">Page not found.</div>', auth=auth))

    def _forbidden(self, auth=None):
        self._send(403, layout("Not allowed", "", '<div class="panel empty">You do not have access to that page.</div>', auth=auth))

    def _serve_static(self, path):
        rel = path[len("/static/"):]
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            return self._not_found()
        ctype = "text/css" if full.endswith(".css") else "application/octet-stream"
        with open(full, "rb") as f:
            self._send(200, f.read(), content_type=ctype)

    # -- helpers --------------------------------------------------------

    def _read_form(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(raw)
        return lambda key, default="": form.get(key, [default])[0]

    def _require_workshop(self, conn, msg=None):
        """Returns auth dict for a logged-in workshop user, or handles the
        redirect/response itself and returns None."""
        auth = get_auth(self, conn)
        if auth is None or auth["user"]["role"] == "platform_admin" or auth["workshop"] is None:
            self._redirect("/login")
            return None
        return auth

    def _require_admin(self, conn):
        auth = get_auth(self, conn)
        if auth is None or auth["user"]["role"] != "platform_admin":
            self._redirect("/login")
            return None
        return auth

    # -- GET --------------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        msg = qs.get("msg", [None])[0]
        err = qs.get("err", [None])[0]

        if path.startswith("/static/"):
            return self._serve_static(path)

        conn = get_conn()
        auth = get_auth(self, conn)

        try:
            if path in ("/", ""):
                if auth is None:
                    return self._send(200, page_landing())
                if auth["user"]["role"] == "platform_admin":
                    return self._redirect("/admin")
                return self._redirect("/tips")

            if path == "/signup":
                if auth: return self._redirect("/tips")
                return self._send(200, page_signup(err))
            if path == "/login":
                if auth:
                    return self._redirect("/admin" if auth["user"]["role"] == "platform_admin" else "/tips")
                return self._send(200, page_login(err))
            if path == "/logout":
                return self._redirect("/", clear_cookie=True)

            # ---- workshop area ----
            if path == "/tips":
                a = self._require_workshop(conn)
                if not a: return
                return self._send(200, page_tips_list(qs, a, msg))
            if path == "/tips/new":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                return self._send(200, page_tip_form(a))
            if path == "/tips/export.csv":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                rows = conn.execute("SELECT id,topic_number,title,category,function_group,control_unit,fault_codes,model_series,symptom,diagnosis,fix,notes,confirm_count,created_at FROM tips ORDER BY created_at DESC").fetchall()
                return self._send(200, csv_bytes(rows), "text/csv", {"Content-Disposition": "attachment; filename=tips_export.csv"})
            if path.startswith("/tips/"):
                a = self._require_workshop(conn)
                if not a: return
                try:
                    tip_id = int(path.split("/")[2])
                except (IndexError, ValueError):
                    return self._not_found(a)
                html_out = page_tip_detail(tip_id, a, msg)
                return self._send(200, html_out) if html_out else self._not_found(a)

            if path == "/cases":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                return self._send(200, page_cases_list(qs, a, msg))
            if path == "/cases/new":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                return self._send(200, page_case_form(a))
            if path == "/cases/export.csv":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                rows = conn.execute("SELECT * FROM cases WHERE workshop_id=? ORDER BY created_at DESC", (a["workshop"]["id"],)).fetchall()
                return self._send(200, csv_bytes(rows), "text/csv", {"Content-Disposition": "attachment; filename=cases_export.csv"})
            if path.startswith("/cases/"):
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                try:
                    case_id = int(path.split("/")[2])
                except (IndexError, ValueError):
                    return self._not_found(a)
                html_out = page_case_detail(case_id, a, msg)
                return self._send(200, html_out) if html_out else self._not_found(a)

            if path == "/dashboard":
                a = self._require_workshop(conn)
                if not a: return
                return self._send(200, page_dashboard(a))

            if path == "/account":
                a = self._require_workshop(conn)
                if not a: return
                return self._send(200, page_account(a, msg))

            if path == "/support":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "premium"):
                    return self._send(200, page_support_locked(a))
                return self._send(200, page_support_list(a, msg))
            if path.startswith("/support/"):
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "premium"):
                    return self._send(200, page_support_locked(a))
                try:
                    ticket_id = int(path.split("/")[2])
                except (IndexError, ValueError):
                    return self._not_found(a)
                html_out = page_support_thread(ticket_id, a, msg)
                return self._send(200, html_out) if html_out else self._not_found(a)

            # ---- admin area ----
            if path == "/admin":
                a = self._require_admin(conn)
                if not a: return
                return self._send(200, page_admin_dashboard(a))
            if path == "/admin/workshops":
                a = self._require_admin(conn)
                if not a: return
                return self._send(200, page_admin_workshops(a, msg))
            if path == "/admin/tickets":
                a = self._require_admin(conn)
                if not a: return
                return self._send(200, page_admin_tickets(qs, a))
            if path.startswith("/admin/tickets/"):
                a = self._require_admin(conn)
                if not a: return
                try:
                    ticket_id = int(path.split("/")[3])
                except (IndexError, ValueError):
                    return self._not_found(a)
                html_out = page_admin_ticket_thread(ticket_id, a, msg)
                return self._send(200, html_out) if html_out else self._not_found(a)

            return self._not_found(auth)
        finally:
            conn.close()

    # -- POST ---------------------------------------------------------------

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        f = self._read_form()
        conn = get_conn()

        try:
            if path == "/signup":
                name = f("workshop_name").strip()
                owner_name = f("owner_name").strip()
                email = f("email").strip().lower()
                password = f("password")
                city = f("city").strip()
                phone = f("phone").strip()
                if not (name and owner_name and email and password):
                    return self._send(200, page_signup("Please fill in all required fields.",
                                                         dict(workshop_name=name, owner_name=owner_name, email=email, city=city, phone=phone)))
                existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
                if existing:
                    return self._send(200, page_signup("That email is already registered. Try logging in instead.",
                                                         dict(workshop_name=name, owner_name=owner_name, email=email, city=city, phone=phone)))
                ts = now_str()
                cur = conn.execute(
                    "INSERT INTO workshops (name, city, contact_email, phone, tier, status, created_at) VALUES (?,?,?,?,'free','active',?)",
                    (name, city, email, phone, ts),
                )
                workshop_id = cur.lastrowid
                h, s = hash_password(password)
                cur2 = conn.execute(
                    "INSERT INTO users (workshop_id, name, email, password_hash, password_salt, role, created_at) VALUES (?,?,?,?,?,'owner',?)",
                    (workshop_id, owner_name, email, h, s, ts),
                )
                conn.commit()
                token = create_session(conn, cur2.lastrowid)
                return self._redirect("/tips?msg=Welcome! You're on the Free tier - browse the Tips library, and upgrade any time from Account.", set_cookie=token)

            if path == "/login":
                email = f("email").strip().lower()
                password = f("password")
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                if not user or not verify_password(password, user["password_hash"], user["password_salt"]):
                    return self._send(200, page_login("Incorrect email or password.", email))
                token = create_session(conn, user["id"])
                dest = "/admin" if user["role"] == "platform_admin" else "/tips"
                return self._redirect(dest, set_cookie=token)

            # ---- workshop actions ----
            if path == "/tips/new":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                topic_number = next_number(conn, "tips", "WN")
                conn.execute(
                    """INSERT INTO tips (topic_number, title, category, subcategory, function_group, control_unit,
                        fault_codes, model_series, symptom, diagnosis, fix, notes, source, workshop_id, created_by,
                        created_at, confirm_count)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (topic_number, f("title"), f("category"), f("subcategory"), f("function_group"), f("control_unit"),
                     f("fault_codes"), f("model_series"), f("symptom"), f("diagnosis"), f("fix"), f("notes"),
                     "Community", a["workshop"]["id"], a["user"]["name"], now_str()),
                )
                conn.commit()
                return self._redirect("/tips?msg=Tip added to the shared library.")

            if path == "/cases/new":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                workshop_id = a["workshop"]["id"]
                n = conn.execute("SELECT COUNT(*) AS n FROM cases WHERE workshop_id=?", (workshop_id,)).fetchone()["n"] + 1
                case_number = f"WS{workshop_id}-CASE-{n:04d}"
                time_val = f("time_spent_hours")
                conn.execute(
                    """INSERT INTO cases (workshop_id, case_number, technician, case_date, vehicle_model, vin, mileage,
                        function_group, control_unit, fault_codes, symptom, diagnosis_steps, root_cause, fix_applied,
                        parts_used, time_spent_hours, status, notes, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (workshop_id, case_number, f("technician"), f("case_date") or datetime.now().strftime("%Y-%m-%d"),
                     f("vehicle_model"), f("vin"), f("mileage"), f("function_group"), f("control_unit"),
                     f("fault_codes"), f("symptom"), f("diagnosis_steps"), f("root_cause"), f("fix_applied"),
                     f("parts_used"), float(time_val) if time_val else None, f("status") or "Open",
                     f("notes"), now_str()),
                )
                conn.commit()
                return self._redirect(f"/cases?msg=Case {case_number} logged.")

            if path.startswith("/cases/") and path.endswith("/promote"):
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                case_id = int(path.split("/")[2])
                case = conn.execute("SELECT * FROM cases WHERE id=? AND workshop_id=?", (case_id, a["workshop"]["id"])).fetchone()
                if case is None:
                    return self._not_found(a)
                topic_number = next_number(conn, "tips", "WN")
                title = f("title") or (case["symptom"] or "")[:70]
                cur = conn.execute(
                    """INSERT INTO tips (topic_number, title, category, subcategory, function_group, control_unit,
                        fault_codes, model_series, symptom, diagnosis, fix, notes, source, workshop_id, created_by,
                        created_at, source_case_id, confirm_count)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (topic_number, title, "", "", case["function_group"], case["control_unit"], case["fault_codes"],
                     case["vehicle_model"], case["symptom"], case["diagnosis_steps"], case["fix_applied"],
                     f"Promoted from a workshop's case log.", "Community", a["workshop"]["id"], a["user"]["name"],
                     now_str(), case["id"]),
                )
                new_tip_id = cur.lastrowid
                conn.execute("UPDATE cases SET linked_tip_id=? WHERE id=?", (new_tip_id, case_id))
                conn.commit()
                return self._redirect(f"/tips/{new_tip_id}?msg=Case {case['case_number']} promoted to shared Tip {topic_number}.")

            if path.startswith("/cases/") and path.endswith("/link"):
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "basic"):
                    return self._redirect("/account")
                case_id = int(path.split("/")[2])
                case = conn.execute("SELECT * FROM cases WHERE id=? AND workshop_id=?", (case_id, a["workshop"]["id"])).fetchone()
                if case is None:
                    return self._not_found(a)
                tip_id = f("tip_id")
                if tip_id:
                    conn.execute("UPDATE cases SET linked_tip_id=? WHERE id=?", (tip_id, case_id))
                    conn.execute("UPDATE tips SET confirm_count = confirm_count + 1 WHERE id=?", (tip_id,))
                    conn.commit()
                return self._redirect(f"/cases/{case_id}?msg=Linked to shared tip and confirmation count updated.")

            if path == "/support/new":
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "premium"):
                    return self._redirect("/account")
                ts = now_str()
                cur = conn.execute(
                    "INSERT INTO tickets (workshop_id, subject, vehicle_model, status, created_at, updated_at) VALUES (?,?,?,'open',?,?)",
                    (a["workshop"]["id"], f("subject"), f("vehicle_model"), ts, ts),
                )
                ticket_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO ticket_messages (ticket_id, sender_role, sender_name, message, created_at) VALUES (?,?,?,?,?)",
                    (ticket_id, "workshop", a["user"]["name"], f("message"), ts),
                )
                conn.commit()
                return self._redirect(f"/support/{ticket_id}?msg=Support ticket sent.")

            if path.startswith("/support/") and path.endswith("/reply"):
                a = self._require_workshop(conn)
                if not a: return
                if not tier_at_least(a["workshop"], "premium"):
                    return self._redirect("/account")
                ticket_id = int(path.split("/")[2])
                ticket = conn.execute("SELECT * FROM tickets WHERE id=? AND workshop_id=?", (ticket_id, a["workshop"]["id"])).fetchone()
                if ticket is None:
                    return self._not_found(a)
                ts = now_str()
                conn.execute(
                    "INSERT INTO ticket_messages (ticket_id, sender_role, sender_name, message, created_at) VALUES (?,?,?,?,?)",
                    (ticket_id, "workshop", a["user"]["name"], f("message"), ts),
                )
                conn.execute("UPDATE tickets SET status='open', updated_at=? WHERE id=?", (ts, ticket_id))
                conn.commit()
                return self._redirect(f"/support/{ticket_id}?msg=Message sent.")

            # ---- admin actions ----
            if path.startswith("/admin/workshops/") and path.endswith("/update"):
                a = self._require_admin(conn)
                if not a: return
                workshop_id = int(path.split("/")[3])
                tier = f("tier")
                status = f("status")
                if tier in TIER_RANK and status in WORKSHOP_STATUSES:
                    conn.execute("UPDATE workshops SET tier=?, status=? WHERE id=?", (tier, status, workshop_id))
                    conn.commit()
                return self._redirect("/admin/workshops?msg=Workshop updated.")

            if path.startswith("/admin/tickets/") and path.endswith("/reply"):
                a = self._require_admin(conn)
                if not a: return
                ticket_id = int(path.split("/")[3])
                ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
                if ticket is None:
                    return self._not_found(a)
                ts = now_str()
                conn.execute(
                    "INSERT INTO ticket_messages (ticket_id, sender_role, sender_name, message, created_at) VALUES (?,'admin',?,?,?)",
                    (ticket_id, a["user"]["name"], f("message"), ts),
                )
                conn.execute("UPDATE tickets SET status='answered', updated_at=? WHERE id=?", (ts, ticket_id))
                conn.commit()
                return self._redirect(f"/admin/tickets/{ticket_id}?msg=Reply sent.")

            if path.startswith("/admin/tickets/") and path.endswith("/close"):
                a = self._require_admin(conn)
                if not a: return
                ticket_id = int(path.split("/")[3])
                conn.execute("UPDATE tickets SET status='closed', updated_at=? WHERE id=?", (now_str(), ticket_id))
                conn.commit()
                return self._redirect(f"/admin/tickets/{ticket_id}?msg=Ticket closed.")

            return self._not_found()
        finally:
            conn.close()


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


if __name__ == "__main__":
    init_db()
    ip = local_ip()
    print("=" * 64)
    print(f"{PLATFORM_NAME} is running.")
    print(f"  Locally:  http://localhost:{PORT}")
    print(f"  On LAN:   http://{ip}:{PORT}")
    print("  For workshops OUTSIDE this network, deploy to a real host - see README.md.")
    print("Press Ctrl+C to stop.")
    print("=" * 64)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()
