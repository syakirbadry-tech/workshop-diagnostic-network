# Workshop Diagnostic Network

A subscription platform for independent Mercedes-Benz workshops: they sign up,
you activate a paid tier, and they get a shared diagnostic knowledge base
plus (on Premium) live in-app support from your team.

- **Free** — browse the shared Tips library (titles/summaries only).
- **Basic** — full Tips detail, private case log for their own technicians,
  and the ability to promote a resolved case into the shared library
  (their workshop name is never shown to other subscribers).
- **Premium** — everything in Basic, plus an in-app support ticket/chat
  thread with you or your technical team.

You (the platform admin) manage everything from `/admin`: activate/suspend
workshops, change their tier, and answer support tickets.

No external Python packages required — just the standard library. This
keeps hosting simple and cheap.

---

## Part 1 — Try it locally first

1. Make sure Python 3.8+ is installed (`python3 --version`).
2. In this folder, run:
   ```
   python3 app.py
   ```
3. On first run, it prints (and saves to `ADMIN_CREDENTIALS.txt`) your
   platform admin login:
   ```
   Email:    syakirbadry@gmail.com
   Password: <randomly generated>
   ```
   **Save that password somewhere safe** — `ADMIN_CREDENTIALS.txt` is only
   written once, on first run.
4. Open `http://localhost:5000` — you'll see the public landing page.
   - Sign up a test workshop to see the subscriber side.
   - Log in with the admin email/password to see `/admin`.
5. There's also a seeded **Demo Workshop** (email `demo@example.com`,
   password `demo1234`, Premium tier) so you can poke around without
   creating your own account.

This local run is only reachable from your own computer — fine for testing,
not for real subscribers. For that, you need Part 2.

---

## Part 2 — Put it on the internet (so workshops elsewhere can sign up)

The app supports two ways of running, both from the exact same `app.py`:

- **Direct script mode** — `python3 app.py` binds its own port. Works on a
  VPS or any host that just runs a long-lived Python process.
- **WSGI mode** — `app.py` also exposes a standard `application(environ,
  start_response)` callable near the bottom of the file. This is what
  free, no-credit-card hosts like PythonAnywhere require, since they run
  your code inside their own web server rather than letting it bind a port.

### Option A: PythonAnywhere (recommended — free, no card required)

This is the easiest fully-free option: no card, and the disk is persistent
by default (nothing gets wiped on redeploy).

1. Go to [pythonanywhere.com](https://www.pythonanywhere.com) and sign up
   for a **Beginner (free)** account — email + password only, no card.
2. Once logged in, open a **Bash console** from the Dashboard and clone your
   GitHub repo:
   ```
   git clone https://github.com/syakirbadry-tech/workshop-diagnostic-network.git
   ```
   (If the repo is private, PythonAnywhere will prompt for a GitHub
   username + a [personal access token](https://github.com/settings/tokens)
   instead of a password.)
3. Go to the **Web** tab → **Add a new web app** → pick your free domain
   (`yourusername.pythonanywhere.com`) → choose **Manual configuration**
   (not a framework preset) → **Python 3.10**.
4. On the Web tab, find **Code → WSGI configuration file** and click it to
   edit. Delete the placeholder content and replace it with:
   ```python
   import sys
   import os

   project_home = '/home/yourusername/workshop-diagnostic-network'
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   os.environ['DB_PATH'] = '/home/yourusername/workshop-diagnostic-network/workshop_network.db'

   from app import application
   ```
   Replace `yourusername` with your actual PythonAnywhere username (shown
   top-right of the dashboard). This tells PythonAnywhere to import
   `application` from `app.py` instead of running it as a script — and
   pins the database to a path on PythonAnywhere's persistent filesystem
   (your account's home directory is never wiped).
5. Back on the **Web** tab, set **Static files**: URL `/static/` → Directory
   `/home/yourusername/workshop-diagnostic-network/static/` (this makes
   `style.css` load fast, though the app can also serve it itself).
6. Click the big green **Reload** button on the Web tab.
7. Visit `https://yourusername.pythonanywhere.com` — that's your public URL,
   shareable with any workshop, anywhere.
8. Grab the admin password: open a Bash console and run
   `cat ~/workshop-diagnostic-network/ADMIN_CREDENTIALS.txt` — it's created
   automatically the first time the app runs (i.e., the first page load
   after your first Reload).
9. **Free-tier limits to know**: PythonAnywhere's free plan sleeps your app
   if it gets no traffic for a while and wakes it on the next request (a
   few seconds' delay), and it doesn't support a custom domain. Both are
   fine to start with — when you have paying workshops, PythonAnywhere's
   paid "Hacker" plan ($5/mo) removes both restrictions and doesn't need a
   different payment flow than any other subscription (a normal card would
   work here, or you can stay on free indefinitely if that's not a
   priority).

### Option B: A basic VPS (DigitalOcean, Linode, a spare machine, etc.)

If you want full control: rent the cheapest droplet/VPS, install Python 3,
copy this folder over (`scp` or `git clone`), and run it permanently with
either `tmux`/`screen` or, better, a `systemd` service so it restarts
automatically on reboot or crash — `python3 app.py` (direct script mode)
is what you'd run here. This avoids any "ephemeral disk" gotchas entirely,
since a VPS's disk is just a normal persistent disk. Ask if you'd like the
exact `systemd` unit file for this — it's a handful of lines.

### Option C: Render.com or Railway.app (need a working card on file)

Same `python3 app.py` direct-script approach works here too (Start Command:
`python3 app.py`, no Build Command needed). Both require a payment card on
file even to provision their free/starter compute, and both wipe local disk
on redeploy unless you pay for a persistent disk/volume with `DB_PATH` (or
Railway's Volume equivalent) pointed at it. Worth revisiting later if you
want a custom domain or more headroom than PythonAnywhere's free tier, but
PythonAnywhere is the simplest path to get live today.

**Whichever host you pick:** don't launch to real paying customers on any
"free tier with ephemeral storage" — you will eventually lose the database.
PythonAnywhere's free tier is persistent by default; Render/Railway's is
not unless you pay for a disk/volume.

---

## Part 3 — Running the business

- **Selling access**: new signups start on Free automatically. When a
  workshop pays you (bank transfer, invoice, whatever you use), open
  `/admin/workshops`, find them, set their **Plan** to Basic or Premium and
  **Status** to Active, and click Save. They'll see the new tier immediately
  on their next page load — no restart needed.
- **Suspending non-payers**: same screen, set Status to Suspended. This
  blocks all paid features until you reactivate them.
- **Answering support tickets**: `/admin/tickets` shows every Premium
  workshop's open tickets across the whole network. Click in to reply.
- **Growing the shared knowledge base**: every workshop's promoted cases
  land in the same Tips library that every other subscriber searches. The
  more workshops you sign up, the more valuable it gets for everyone — this
  is the core value loop of the product.
- **Backups**: `workshop_network.db` is the entire database. Download/copy
  it periodically (however your host lets you access files) and keep dated
  backups somewhere safe.
- **CSV export**: Basic+ workshops can export their own Tips view and case
  log as CSV from the Tips Library / Case Log pages — handy if you want to
  pull data into Excel for your own reporting too.

---

## Security notes (read before going live)

- Passwords are stored properly hashed (PBKDF2-SHA256, salted) — never in
  plain text.
- Login sessions last 14 days and are stored server-side; logging out
  invalidates the cookie.
- There's no payment processing built in — billing is manual, as agreed.
  If you later want self-serve card payments, this can be wired up to
  Stripe (you'd need to create a Stripe account and give me the API keys —
  I can't create the account on your behalf).
- There's no CSRF token on forms yet (a reasonable next hardening step
  before heavy real-world use, but low risk for an MVP with a small number
  of trusted subscribers).
- Always run behind HTTPS in production — Render and Railway both provide
  this automatically on their generated domains and on custom domains.
- This is an independent tool for managing your own workshop network's
  diagnostic knowledge. It is not affiliated with, endorsed by, or
  officially connected to Mercedes-Benz — keep marketing language and any
  public-facing copy clear on that point.

---

## Editing the platform

A few things you'll likely want to tweak in `app.py` (all near the top of
the file, look for the `Configuration` section):

```python
PLATFORM_NAME = "Workshop Diagnostic Network"   # your brand name
PLATFORM_TAGLINE = "..."                        # landing page subheading
TIER_PRICE = {"free": "RM 0/mo", "basic": "RM 99/mo", "premium": "RM 249/mo"}
```

The dropdown lists for symptom categories, function groups, and case
statuses are also editable lists near the top of `app.py`.

## Files in this folder

- `app.py` — the entire application (routing, pages, auth, tiering, admin).
  Runs standalone with `python3 app.py`, and also exposes a WSGI
  `application` callable for hosts like PythonAnywhere that import it as a
  module instead.
- `schema.sql` — database structure, applied automatically on first run.
- `static/style.css` — all styling.
- `workshop_network.db` — created automatically on first run; this is your
  live data (workshops, users, tips, cases, tickets).
- `ADMIN_CREDENTIALS.txt` — created on first run with your admin login.
  Keep this private; delete it once you've saved the password elsewhere.
