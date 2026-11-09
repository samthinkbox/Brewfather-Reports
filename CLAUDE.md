# Brewfather Reports – project context

Reports on a home brewer's own brewing history, pulled from their Brewfather account through
the Brewfather API v2: hops, malt, water, results, recipe versions and hop flavour charts.
It is packaged so others can run their own copy with their own User ID and API key.
Version is in `app/version.py` (1.0.0). © 2026 Thinkbox, MIT licence (`LICENSE`, `TERMS.md`).
Repo: https://github.com/samthinkbox/Brew-History-for-Brewfather (branch `main`).

## Ground rules
- **Python standard library only.** No pip dependencies, no build step, no JS frameworks.
- **Never put credentials in code or commits.** The Brewfather User ID and API key live only in
  `.env` (written by the Admin page, gitignored). `data/`, `output/`, `Next Brew/`, `dist/`
  and `HopDatabase/` are private or generated and also gitignored.
- Commit and push only when the owner asks.
- Scripts are run with `python -I`, so every module in `app/` does
  `sys.path.insert(0, HERE)` before importing its siblings. Keep that pattern.
- All file locations come from `app/paths.py`. Don't hard-code paths elsewhere.
- On this Windows machine git is at `/c/Program Files/Git/cmd/git.exe`. Push with
  `GIT_TERMINAL_PROMPT=0`; credentials are cached by Git Credential Manager.

## Layout
| Path | What it is |
|---|---|
| `start.bat`, `start.sh` | Run `app/server.py`. |
| `app/server.py` | Stdlib web server: Admin page, report pages, JSON API, job runner, scheduler. |
| `app/run.py` | Fetch and build without the server (`--offline`, `--update-hops`). |
| `app/fetch.py`, `app/brewfather.py` | API calls; writes `data/batches.json`. `load_env()` reads `.env`. |
| `app/build_report.py` | Turns batches into `output/report.html`, `versions.html` and the CSVs. |
| `app/hop_flavour.py` | Hop aroma profiles from HopDatabase data. |
| `app/layout.py` | Shared header/nav, footer, CSS; builds `admin.html` and `terms.html`. |
| `app/settings.py` | `data/settings.json` with `DEFAULTS` (only known keys are kept). |
| `app/next_brew.py` | Owner's personal recipe ideas; `--upload` creates them in Brewfather. Not in the package. |
| `templates/` | Page templates (`report.html`, `versions.html`, `next_brew.html`, `admin.html`, `terms.html`) and `hop_radar.js`. |
| `config/` | Owner-editable files, also editable on the Admin page: `exclude.txt`, `families.txt`, `hop_flavours_extra.json`. |
| `hopdata/` | Copy of HopDatabase `hops.json` (MIT, © 2024 Kasper Andreas Rømer Grøntved). |
| `docs/` | `DEPLOY.md` (Caddy, nginx, IIS, systemd, Plesk) and `brew-reports.service`. |
| `tools/build_package.py` | Builds `dist/brewfather-reports-<version>.zip`; leaves out `.env`, `data/`, `output/`, `next_brew.py`, `exclude.txt`, `families.txt`. |

Pages are built as static HTML: the Python side computes a JSON payload and injects it into a
template; the template's own JS renders tables and SVG charts. `layout.apply(page, active)`
adds the header, CSS and footer to every page.

## Server (`app/server.py`)
- `python app/server.py [--host H] [--port 8765] [--proxy]`. `--proxy` trusts forwarded
  headers when behind nginx/Caddy/IIS.
- First run prints a one-time **setup code**; the Admin page asks for it, the Brewfather
  credentials and an admin password (PBKDF2 hash in `data/admin.json`).
- Sessions: HttpOnly, SameSite=Strict cookie. Every POST must send header `X-BFR: 1`.
  5 failed logins lock that address for 5 minutes.
- Only `output/*.html|.csv|.png|.svg` are served; report pages need sign-in unless
  `public_reports` is on.
- `JOBS`: refresh (`run.py`), rebuild (`--offline`), hops (`--update-hops`), upload
  (`next_brew.py --upload`), run as subprocesses with a live log.
- `EDITABLE` lists the config files the Admin page may edit (size-limited; JSON validated).
- On Windows the socket uses `SO_EXCLUSIVEADDRUSE`: plain `SO_REUSEADDR` let two servers share
  port 8765 and caused "setup code is wrong". Don't remove it.

## Brewfather API facts
- Recipes support POST and PATCH; batch PATCH only accepts status and measured fields.
- **No image upload**, so hop charts are offered as "Save PNG for Brewfather" (1000 × 1000).
- Rate limit 500 calls per hour. A batch note containing `#noreport` excludes that brew.
- Colours are stored as SRM; reports show EBC (× 1.97).

## Calculation rules
- **Hop strength:** Cryo, lupulin and LupuMax (`hop_form(h) == "Cryo / lupulin"`) are double
  strength (`CONCENTRATED_STRENGTH = 2.0`). Each hop has `g` (weighed), `strength`,
  `ge` = g × strength (pellet-equivalent) and `gpl` = ge / batch size.
  - Use `ge` for every rate and comparison: g/L tiles, hop amounts by variety, combos,
    heatmap, rate charts, bittering table, brew log, Recipe versions stage rates, flavour weights.
  - Recipe listings show weighed grams with `pellet(h)` beside them: "30 g (≈ 60 g pellets)".
    The original recipe amounts must stay visible. The "Hops used" kg total stays as weighed.
  - IBU is Brewfather's own figure and is not changed.
  - CSVs: `hops.csv` has `pellet_equiv_g`; `brews.csv` has `total_hops_pellet_equiv_g`, and its
    g/L columns are pellet-equivalent.
- **Flavour profile** (`hop_flavour.profile`): nine aromas (Citrus, Resin/Pine, Spice, Herbal,
  Grassy, Floral, Berry, Stone Fruit, Tropical Fruit), 0–5. Weighted average by `ge` ×
  stage weight: Whirlpool and Dry hop 1.0, Late boil 0.5, bittering excluded. `parts` gives each
  hop's share per aroma (adds up to `aromas`); beyond `MAX_PARTS = 6` hops the rest become "Other".
  Missing hops are reported; add them (or aliases) in `config/hop_flavours_extra.json`.
- **Water:** the owner's tap sources are matched by the `water_pattern` regex setting
  (e.g. `townname|bore`, label "Town supply"). Empty pattern means every source; empty
  label reads "tap".
- **Recipe versions:** brews grouped into families by `config/families.txt`.

## Charts
- `templates/hop_radar.js` (`window.HopRadar`): `inline` radar, `makeup` stacked bars per aroma
  split by hop, `card` for the PNG, `savePng`.
- Hop colours use a validated categorical palette, in fixed order, as CSS variables
  `--hop1..6` and `--hop-other`. Light: #2a78d6 #eb6834 #1baf7a #eda100 #e87ba4 #008300.
  Dark: #3987e5 #d95926 #199e70 #c98500 #d55181 #008300. Other: #b5b3ab / #5c5b57.
  Follow the dataviz rules: 2px gaps between segments, hover tooltips, legend, one axis.
- Every page supports light and dark mode.

## Checking a change
There is no test suite. After a change:
1. `python -I app/run.py --offline` to rebuild from saved data.
2. Serve `output/` (e.g. `python -I -m http.server 8798 --directory output`) and look at the
   pages in a browser for console errors and layout overflow; stop the server afterwards.
   `file://` previews don't work well in the browser pane.
3. Check numbers against a few known brews.
4. `python tools/build_package.py` if packaging changed.

## Deployment
Runs locally (start.bat, http://localhost:8765) or on a public server behind a reverse proxy
with HTTPS; see `docs/DEPLOY.md` (Caddy, nginx, IIS, systemd, Plesk).
