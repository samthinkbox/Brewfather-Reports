# Brewfather Reports – project context

Reports on a home brewer's own brewing history, pulled from their Brewfather account through
the Brewfather API v2: hops, malt, water, results, recipe versions and hop flavour charts.
It is packaged so others can run their own copy with their own User ID and API key.
Version is in `app/version.py` (1.2.0). © 2026 Thinkbox, MIT licence (`LICENSE`, `TERMS.md`).
Repo: https://github.com/samthinkbox/Brew-History-for-Brewfather (branch `main`).

## Ground rules
- **Python standard library only.** No pip dependencies, no build step, no JS frameworks.
- **Never put credentials in code or commits.** The Brewfather User ID and API key, and any AI reader
  key (`AI_KEY_CLAUDE`, `AI_KEY_CHATGPT`, `AI_KEY_GEMINI`, `AI_KEY_COPILOT`), live only in
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
| `install-app.bat`, `install-app.sh` | `desktop.py --install --autostart`: Start Menu/desktop shortcuts (Windows), app menu entry (Linux). |
| `app/desktop.py` | Desktop launcher: starts the server detached (pythonw, log in `data/server.log`, pid/port/stop token in `data/desktop.json`) and opens Edge/Chrome with `--app=`. `--install`, `--uninstall`, `--stop`, `--server-only`. |
| `app/pwa.py` | Installable-app files served without sign-in: `manifest.webmanifest`, `sw.js` (`templates/sw.js`, offline note only, no cache) and icons in `templates/icons/` (drawn by `tools/make_icons.py`). `layout.HEAD` links them. |
| `app/server.py` | Stdlib web server: Admin page, report pages, JSON API, job runner, scheduler. |
| `app/run.py` | Fetch and build without the server (`--offline`, `--update-hops`). |
| `app/fetch.py`, `app/brewfather.py` | API calls; writes `data/batches.json`. `load_env()` reads `.env`. |
| `app/build_report.py` | Turns batches into `output/report.html`, `versions.html` and the CSVs. |
| `app/hop_flavour.py` | Hop aroma profiles from HopDatabase data. |
| `app/layout.py` | Shared header/nav, footer, CSS; builds `admin.html`, `import.html` and `terms.html`. |
| `app/brew_calc.py` | Shared maths: gravity, Morey colour, Tinseth, water/salt plan, `water_block`, ingredient `library`. Used by next_brew and recipe_import. |
| `app/recipe_parse.py` | Rule-based reader for pasted recipes (`parse`), `from_json` (AI or pasted JSON), the AI `prompt`. |
| `app/recipe_import.py` | Import page logic: template, matching, `questions`, `build` (scaling + Brewfather recipe), `upload`. |
| `app/ai_parse.py` | Optional AI reader: Claude, ChatGPT, Gemini, Copilot (GitHub Models / Azure OpenAI) over urllib. |
| `app/settings.py` | `data/settings.json` with `DEFAULTS` (only known keys are kept). |
| `app/next_brew.py` | Owner's personal recipe ideas; `--upload` creates them in Brewfather. Not in the package. |
| `templates/` | Page templates (`report.html`, `versions.html`, `next_brew.html`, `import.html`, `admin.html`, `terms.html`) and `hop_radar.js`. |
| `config/` | Owner-editable files, also editable on the Admin page: `exclude.txt`, `families.txt`, `hop_flavours_extra.json`. |
| `hopdata/` | Copy of HopDatabase `hops.json` (MIT, © 2024 Kasper Andreas Rømer Grøntved). |
| `docs/` | `DEPLOY.md` (Caddy, nginx, IIS, systemd, Plesk) and `brew-reports.service`. |
| `tools/build_package.py` | Builds `dist/brewfather-reports-<version>.zip`; leaves out `.env`, `data/`, `output/`, `next_brew.py`, `exclude.txt`, `families.txt`. New `app/` modules must be added to its `FILES`. |

Pages are built as static HTML: the Python side computes a JSON payload and injects it into a
template; the template's own JS renders tables and SVG charts. `layout.apply(page, active)`
adds the header, CSS and footer to every page.

## Server (`app/server.py`)
- `python app/server.py [--host H] [--port 8642] [--proxy]`. `--proxy` trusts forwarded
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
- Desktop launcher hand-off: env `BFR_SETUP_CODE` (the launcher's setup code, given to the window as `admin.html#setup=…`)
  and `BFR_STOP_TOKEN` (enables `POST /api/stop` from 127.0.0.1 with that token). Both are popped from the environment.
- On Windows the socket uses `SO_EXCLUSIVEADDRUSE`: plain `SO_REUSEADDR` let two servers share
  a port and caused "setup code is wrong". Don't remove it.

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

## Import recipe (`import.html`, `recipe_import.py`)
- Flow: `/api/import/read` (rules, AI, or chat-AI JSON pasted back) → draft → `auto_match` → `questions`
  (first option = recommended; `answer_defaults` applies them) → `/api/import/check` re-runs with the edited
  draft → `/api/import/upload` POSTs a **new** recipe (never PATCHes existing ones; refuses while questions are open).
  The page keeps the draft; the server re-validates it with `sanitize` every time.
- Draft answers live on the draft: `match` = one of your ingredient names, `"new"`, or `"template"` (yeast);
  `set_kg` / `set_g` / `others.N.set` = the user's own amounts; hop `charge` (1st/2nd/3rd dry hop) and `day`; `mash_choice`.
  Paths like `hops.3.match` are set by the page.
- **The owner's rule: follow the pasted recipe.** Same ingredients, additions, stages and times; only amounts change.
  Never substitute automatically: `auto_match` uses one of your ingredients only when it is the same thing
  (`same_malt`: token subset + category + colour; `same_hop`: variety + form, pellet = whole cone), otherwise `"new"`.
  Swaps are only the user's choice: the row menu, or the malt question. Malts: an identical malt is taken without asking only
  if used in 3+ brews; otherwise, when you have malts of the same family (`malt_family`: pilsner, ale, 2-row, pale = one
  "base" family), it's a question offering that family most-used first (e.g. a pilsner malt and an ale malt the user
  treats as interchangeable), "keep as written", and a select of every malt. Menus list every malt and hop from past brews.
- Standard ("template") recipe: setting `import_template_id` (chosen on Admin, saved to `data/import_template.json`),
  else the latest brew's recipe. Its batch size, efficiency, boil, equipment, mash, fermentation, folder, water, miscs
  and yeast amount are applied. Style fields are dropped.
- Scaling: every hop and other addition × (your batch ÷ recipe batch); grain × the factor that reaches the recipe's OG
  (or its efficiency, else 72%) at your size and efficiency. Hop times stay as written (warning if longer than your boil).
  Option `ibu: "match"` rescales only the bittering additions to the stated IBU. A hop in another form or swapped is adjusted by
  strength (cryo / lupulin = half the pellet weight, pellets = double the cryo weight) and, for a bittering hop swapped to
  another variety, by alpha acid compared at pellet strength (alpha ÷ strength). Swap menus list every malt and hop
  from past brews (`allMalts`, `allHops`; oils and extracts left out). Recipe "others" become Brewfather miscs, scaled; water salts and priming are not added.
- Yeast: strain code, then `YEAST_FAMILIES` (asked as a question). Swap menus offer aroma-similar hops (HopDatabase
  cosine) and the owner's usual bittering hops.
- Test recipe: Craft Beer & Brewing "Readers' Choice WCIPA" (19 L, whole-cone, hopback, "each", ml AQB, numbered dry hops).
- AI reader is opt-in (`ai_provider` setting). Endpoints must be https (or http://localhost). Default model names will age;
  they're editable on the Admin page. The connectors were checked against a fake local server, not the real providers.
- Tests: there is no suite; sample recipes (BYO-style imperial, BeerSmith printout, NZ metric prose, web table) were used.

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
5. Don't test uploads against the owner's Brewfather account: replace `recipe_import.upload` with a stub that saves the
   recipe JSON (and run a test server on another port with a known session token).

## Deployment
Runs locally (start.bat, http://localhost:8642) or on a public server behind a reverse proxy
with HTTPS; see `docs/DEPLOY.md` (Caddy, nginx, IIS, systemd, Plesk).
