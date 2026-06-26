# Minnesota Lake & Fish Finder

An interactive map of Minnesota lakes scored for five sport species
(walleye, muskie, northern pike, largemouth bass, smallmouth bass), built from
Minnesota DNR fisheries survey data. Color any lake by **population density**,
**biggest fish**, or **pressure trend**, filter by **mean depth**, and overlay
the state's **highways & interstates**.

---

## What's in this folder

```
index.html                 the app (open this)
data/
  full_lakes.json          lake + fish data the map reads
  mn_boundary.json         Minnesota state outline
  mn_roads.json            interstates & highways
mn_lakefinder_pull.py      script to refresh data from the live DNR API
dows.txt                   list of lake IDs (DOW numbers) to pull
README.md                  this file
.gitignore
```

---

## 1. Run it locally

The map loads the `data/*.json` files at runtime. Browsers block that when you
double-click the HTML (the `file://` security rule), so run a tiny local server.

In VS Code: **Terminal → New Terminal**, then from this folder:

```bash
python3 -m http.server 8000
```

Open **http://localhost:8000** in your browser. That's it.

(If `python3` isn't found, install Python 3.8+ from https://python.org and
reopen the terminal. No packages to install — everything uses the standard
library.)

> Tip: the VS Code extension **"Live Server"** does the same thing with a click
> if you prefer not to use the terminal.

---

## 2. Refresh the data from the live DNR API

The included `data/full_lakes.json` is built from a **2023 data snapshot**.
To pull newer surveys (2024+, as the DNR posts them — they lag several months),
use the puller script.

**Step A — confirm the API's structure (do this once):**

```bash
python3 mn_lakefinder_pull.py inspect 11020300
```

This fetches one lake (Leech Lake), saves the raw JSON to `raw_cache/`, and
prints the field names. If they differ from the `FIELDS` map at the top of
`mn_lakefinder_pull.py`, edit that map. (Send the printout to your collaborator
if unsure.)

**Step B — bulk pull and rebuild the data file:**

```bash
python3 mn_lakefinder_pull.py pull \
    --dows dows.txt \
    --out  data/full_lakes.json \
    --morph data/full_lakes.json \
    --since 2015 --recent 2019
```

- `--since 2015`  earliest survey year used for the trend lines
- `--recent 2019` earliest year counted as "current" for density/size
- `--morph`       carries lat/lon, depth, county from the existing file
  (the API doesn't always include those)

Re-runs are fast: every lake's raw response is cached in `raw_cache/`.
Delete that folder (or pass `inspect` with a fresh lake) to force re-download.

**Pull the whole state (~4,500 lakes):** replace `dows.txt` with a bigger list
of DOW numbers. You can get every DOW from the `consolidated_lake_data.csv`
in the source dataset, or from the DNR's by-name / by-point API.

After a pull, just refresh the browser — the map reads the new file.

---

## 3. Push to GitHub & host privately

```bash
git init
git add .
git commit -m "Minnesota lake & fish finder"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

**Hosting for a select few — options:**

- **GitHub Pages (private-ish):** Settings → Pages → deploy from `main`.
  On a Pages site the `data/*.json` files load automatically (it's served over
  https). Note: with a *free* GitHub account Pages sites are public; to restrict
  access use a **private repo on GitHub Team/Enterprise** (which supports
  private Pages), or one of the options below.
- **Netlify / Cloudflare Pages:** drag-and-drop or connect the repo; both offer
  password protection / access control on paid tiers.
- **Vercel:** connect the repo; supports password protection and SSO gating.

Because the app is fully static (just HTML + JSON + CDN libraries), any static
host works. There's no backend to run.

---

## Data notes & honesty

- Lake morphology, catch rates (CPUE), weights, lengths, and survey history come
  from **MN DNR fisheries surveys**. The bundled file is a 2023 snapshot; the
  puller refreshes from the live API.
- **Population density** = total fish caught ÷ lake square miles. It reflects
  survey effort, so read it as relative abundance, not a literal headcount.
- **Biggest fish** = average weight for bass & walleye; average length for pike
  & muskie.
- **Pressure trend** = slope of catch rate across surveys (2015+).
- Lakes are shown as center points sized by area, not traced shorelines.
- State outline and highways are from **Natural Earth**; satellite imagery is
  live **Esri World Imagery** tiles.
- The DNR retains copyright on lake data — fine for personal/educational use;
  review the DNR General Data & Software License Agreement before any public or
  commercial deployment.
```