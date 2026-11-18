# Brewfather Reports

© 2026 Thinkbox · MIT licence · see [TERMS.md](TERMS.md). Not affiliated with or endorsed by Brewfather.

Reports on your own brewing history from your Brewfather account: hops, malt, water, results,
recipe versions and hop flavour charts. It runs on your own computer or server with your own
Brewfather API key, and your data stays there.

## Install

1. Install Python 3.9 or newer (python.org). Nothing else is needed: it uses only Python's standard library.
2. Unzip the package somewhere, for example `Documents/Brewfather Reports`.
3. Start it: double-click `start.bat` (Windows), or run `./start.sh` (Mac/Linux), or `python app/server.py`.
4. Open http://localhost:8765/admin.html. The first time, it asks for:
   - the **setup code** shown in the server window,
   - your **Brewfather User ID and API key** (Brewfather › Settings › API › Generate key; give it read access
     to batches and recipes),
   - an **admin password** for this site.

It then downloads your brews and builds the pages. Keep the server window open while you use it.

To run it on a public web server, including a Plesk VPS alongside other sites, see [docs/DEPLOY.md](docs/DEPLOY.md).

## Pages

Every page has the same header with links to the others.

- **Brew History** (`report.html`)
  - **Hops:** amounts by stage (bittering, late boil, whirlpool, dry hop), and the flavour/aroma hop
    pairs, trios and full line-ups you use. Cryo, lupulin and LupuMax hops are double strength, so hop
    amounts, every g/L rate and the flavour charts count them as twice their weight in pellets. Recipe
    listings still show the grams you weighed, with the pellet equivalent beside them (e.g. "30 g (≈ 60 g
    pellets)"), and IBU comes from Brewfather unchanged.
  - **Bittering hops:** a table of usage, alpha acid, IBU and timing.
  - **Malt bill:** the make-up of each grist.
  - **Water:** your tap water profiles, final Ca/Mg/Cl/SO4 per brew, the SO4:Cl ratio (e.g. 5.5:1), planned
    vs measured mash pH, salts and acid per brew, and suggested salts for West Coast IPA, NEIPA and pale ale.
  - **Results:** planned vs actual ABV and efficiency, and attenuation.
  - **Brew log:** click a brew for its details and a hop flavour chart.
- **Recipe versions** (`versions.html`): what changed between brews of the same recipe, and how it turned out.
- **Import recipe** (`import.html`): paste a recipe from a magazine, book or web page and create it in Brewfather, scaled to your
  batch. See below.
- **Admin:** see below.
- **Terms:** copyright, licence and terms of use.

CSV files (`brews.csv`, `hops.csv`, `malts.csv`) can be downloaded from the Admin page.

## Admin page

- **Data:** fetch from Brewfather and rebuild, rebuild from saved data, or update hop data. The progress log shows as it runs.
- **Brews in the reports:** untick a brew to leave it out (it is saved in `exclude.txt`). Brews with `#noreport` in a
  Brewfather batch note are always left out.
- **Settings:**
  - Site title.
  - Your tap water profile, used in the Water section.
  - Automatic fetching (every 1 to 24 hours, while the server runs).
  - Whether the report pages need a sign-in. They are private by default.
- **Recipe families:** which brews belong together on the Recipe versions page (`families.txt`).
- **Extra hop flavours:** aroma scores for hops HopDatabase doesn't have (`hop_flavours_extra.json`).
- **Standard recipe for imports:** the Brewfather recipe that imported recipes are built on (your latest brew if you don't choose one).
- **AI recipe reader (optional):** connect Claude, ChatGPT, Gemini or Copilot with your own API key.
- **Brewfather connection:** test the connection, or change the User ID and API key.
- **Downloads:** the CSV files and a JSON backup of every batch.
- **Admin password:** change it, or sign out.

## Hop flavour charts

The charts use the aroma scores (Citrus, Resin/Pine, Spice, Herbal, Grassy, Floral, Berry, Stone Fruit,
Tropical Fruit, 0 to 5) from the [HopDatabase](https://github.com/kasperg3/HopDatabase) project, which
collects them from hop suppliers. A brew's chart averages its hops by grams:

- whirlpool and dry hops count in full,
- late-boil hops count half,
- bittering hops are left out.

**Save PNG for Brewfather** saves the chart as a 1000 × 1000 image to add to a recipe or batch in the
Brewfather app. Brewfather's API can't upload images.

## Import recipe

Paste a whole recipe (title, batch size, OG/FG/IBU, malts, hops, yeast, method) and click **Read recipe**.

1. **Reading.** By default the recipe is read on your own server. It understands metric and imperial amounts, US and imperial
   gallons, °F, tables copied from web pages, and BeerSmith/Brewfather-style printouts. Anything it can't settle becomes a question
   for you, with a recommended answer. Tick "Answer the questions for me" to take the recommended answers straight away.
2. **Matching.** Malts, hops and yeast are matched to ingredients you have used before: crystal malts by colour, yeasts by strain
   or family (for example WLP001 → your US-05). A hop you have never used can be kept as a new ingredient or swapped for one of
   yours with a similar aroma. A bittering hop can be swapped for your usual bittering hop.
3. **Scaling to your batch.**
   - The grain bill is scaled to reach the recipe's OG at your batch size and efficiency. If the recipe has no OG, its efficiency
     (or 72%) is used to work it out.
   - Bittering hops are scaled to the recipe's IBU, using your hops' alpha acids. Boil additions longer than your boil are
     shortened, and the bittering makes up the difference.
   - Whirlpool and dry hops keep the same grams per litre. Cryo and lupulin count as double strength.
   - Hop totals are shown in 100 g bags.
4. **Your standards.** Equipment, mash, fermentation, folder and usual yeast come from your standard recipe (Admin page). For
   water you can keep your standard salts, or get salts worked out for West Coast IPA, NEIPA or pale ale on your own water.
5. **Review and create.** Every match and amount can be changed. **Create in Brewfather** adds it as a new recipe; it never
   changes your existing recipes. The Brewfather API key needs permission to edit recipes.

**Optional AI reader.** On the Admin page you can connect Claude (Anthropic), ChatGPT (OpenAI), Gemini (Google) or Copilot (GitHub
Models, or your own Azure OpenAI deployment) with your own API key. The model and API address can be changed; a local model server
on `http://localhost` with an OpenAI-style API also works under "Copilot". The AI reads the recipe and picks your matching
ingredients, and the same questions and review follow. Without a key, **Copy a prompt for my own chat AI** gives you a prompt to
paste into any chat AI in your browser; paste its JSON answer back into the box and click Read recipe.

## Command line

```
python app/server.py              # the web server (Admin page and reports); start.bat / start.sh run this
python app/run.py                 # fetch from Brewfather and build the pages without the server
python app/run.py --offline       # rebuild from the saved data
python app/run.py --update-hops   # download the latest HopDatabase hop data first
```

## Folders

| Folder or file | What it is |
|---|---|
| `start.bat`, `start.sh` | Start the server (Windows, Mac/Linux). |
| `app/` | The Python program. `paths.py` says where everything lives. |
| `templates/` | The page templates (Brew History, Recipe versions, Import recipe, Admin, Terms) and the hop chart script. |
| `config/` | Files you edit, also editable on the Admin page: `exclude.txt` (brews left out), `families.txt` (recipe families), `hop_flavours_extra.json` (extra hop flavours). |
| `hopdata/` | Hop aroma data from HopDatabase (MIT licence). |
| `docs/` | Running it on a public web server ([DEPLOY.md](docs/DEPLOY.md)). |
| `tools/` | Building the installable package. |
| `.env` | Your Brewfather User ID and API key, and any AI reader key (written by the Admin page). Keep it private. |
| `data/` | Downloaded brews, settings and the admin password hash. Back it up; never share it. |
| `output/` | The built pages. |

Colours are shown in EBC (Brewfather stores SRM; EBC = SRM × 1.97).

## Next Brew (optional, personal)

`app/next_brew.py` builds recipe ideas from your own brews and can create them in Brewfather
(`python app/next_brew.py --upload`). Its recipes, water profile and template recipe are set up for one
brewer, so it is not included in the package. Turn the page on in Admin › Settings once you have adapted it.

## Building the package

`python tools/build_package.py` writes `dist/brewfather-reports-<version>.zip`. It leaves out `.env`, `data/`,
`output/`, `next_brew.py` and your `exclude.txt` and `families.txt`. The version number is in `app/version.py`.


## Shutting app down

Click in the cmd window where the server is running and press Ctrl+C. The server stops, and the window shows “Press any key to continue”. Press any key to close it.

You can also just close the cmd window with its X. Nothing is lost, because your data and settings are already saved in the folder.

Some things to know:

If you close it while a fetch or rebuild is running, that run is cut short. Click the button again next time.
Automatic fetching only happens while the server is running.
To look at your history again, double-click start.bat and open http://localhost:8765. You’ll only need your admin password; the setup code was for the first time.