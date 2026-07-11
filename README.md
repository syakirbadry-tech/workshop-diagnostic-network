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

This app is a plain Python script that listens on a port — it runs
perfectly on any host that can run `python3 app.py` continuously. Two good
options, easiest first:

### Option A: Render.com (recommended to start)

1. Put this folder in a GitHub repository (create a free GitHub account if
   needed, create a new repo, upload these files).
2. Go to [render.com](https://render.com), sign up, click **New → Web
   Service**, and connect your GitHub repo.
3. Configure:
   - **Runtime**: Python 3
   - **Build Command**: *(leave blank — no dependencies to install)*
   - **Start Command**: `python3 app.py`
   - Render automatically sets a `PORT` environment variable; this app
     already reads it (`app.py` uses `os.environ.get("PORT", 5000)`), so no
     changes needed.
4. **Important — add a persistent disk.** By default, Render's filesystem is
   wiped every time you redeploy, which would delete your database. Under
   your service's **Disks** settings, add a persistent disk (a few dollars a
   month for 1 GB is plenty for a long time) mounted at, e.g., `/data`. Then
   add an environment variable so the app stores its database there instead
   of next to the code:
   - **Key**: `DB_PATH`   **Value**: `/data/workshop_network.db`
   - The app already reads this variable (see `DB_PATH` near the top of
     `app.py`) — no code changes needed, just set the environment variable
     in Render's dashboard.
5. Deploy. Render gives you a public URL like `https://your-app.onrender.com`
   — that's what you share with workshops.
6. Check the **Logs** tab after first deploy to grab the admin password
   that gets printed on first run (do this immediately, since free/starter
   log retention is limited).
7. Add a custom domain later under **Settings → Custom Domains** if you want
   something like `app.yourbrand.com`.

### Option B: Railway.app

Same idea as Render — connect your GitHub repo, set the start command to
`python3 app.py`, and attach a persistent **Volume** (Railway's equivalent
of a persistent disk) so `workshop_network.db` survives redeploys.

### Option C: A basic VPS (DigitalOcean, Linode, a spare machine, etc.)

If you want full control: rent the cheapest droplet/VPS, install Python 3,
copy this folder over (`scp` or `git clone`), and run it permanently with
either `tmux`/`screen` or, better, a `systemd` service so it restarts
automatically on reboot or crash. This avoids any "ephemeral disk" gotchas
entirely, since a VPS's disk is just a normal persistent disk. Ask if you'd
like the exact `systemd` unit file for this — it's a handful of lines.

**Whichever host you pick:** don't launch to real paying customers on any
"free tier with ephemeral storage" — you will eventually lose the database.
Either pay for persistent disk/volume, or use a VPS.

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
- `schema.sql` — database structure, applied automatically on first run.
- `static/style.css` — all styling.
- `workshop_network.db` — created automatically on first run; this is your
  live data (workshops, users, tips, cases, tickets).
- `ADMIN_CREDENTIALS.txt` — created on first run with your admin login.
  Keep this private; delete it once you've saved the password elsewhere.
