#!/usr/bin/env python3
"""
enrich_lakes.py
===============
Add two angler-facing layers to data/full_lakes.json WITHOUT re-pulling the
survey/morphology data:

  * access  — public boat/water access, parsed from the survey responses already
              in raw_cache/ (the `accesses` array). No new requests.
  * stocking — recent DNR fish-stocking history, scraped per lake from the
               LakeFinder stocking report page (HTML table keyed by DOW):
                 https://www.dnr.state.mn.us/lakefind/showstocking.html?downum=<DOW>
               Cached to raw_cache/<DOW>_stocking.html so re-runs are fast.

Stocking is treated as *context*, not a score: we keep recent records (year,
species, size, number) and the most-recent year per species. The map uses this
to label a fishery natural-vs-stocking-supported and to show a boat icon.

USAGE
-----
    python3 enrich_lakes.py              # enrich data/full_lakes.json in place
    python3 enrich_lakes.py --limit 20   # test on first 20 lakes
    python3 enrich_lakes.py --no-stocking  # access only (no network)

Standard library only.
"""
import argparse, json, os, re, sys, time, html, urllib.request, urllib.error

CACHE_DIR = "raw_cache"
STOCK_URL = "https://www.dnr.state.mn.us/lakefind/showstocking.html?downum={dow}"
SINCE_YEAR = 2015          # keep stocking records from this year on
MAX_RECENT = 8             # cap stored recent records per lake

# DNR owner-type codes seen in the survey `accesses` array -> readable label
OWNER = {"DNR": "State (DNR)", "COU": "County", "CIT": "City",
         "FED": "Federal", "TWP": "Township", "OTH": "Other", "PVT": "Private"}

# Trophy ("monster") length thresholds in inches, per species. A fish at/above
# this length counts as a trophy. Bass are by length too (the survey records
# per-fish length but only an AVERAGE weight, so a weight-based count isn't
# possible). Tuned against the statewide length distributions.
MONSTER_LEN = {"Walleye": 26, "Northern Pike": 34, "Muskie": 45,
               "Largemouth Bass": 19, "Smallmouth Bass": 18}
SPCODE = {"WAE": "Walleye", "NOP": "Northern Pike", "MUE": "Muskie",
          "LMB": "Largemouth Bass", "SMB": "Smallmouth Bass"}


def _year_of(s):
    for tok in str(s).replace("/", "-").split("-"):
        if tok.isdigit() and len(tok) == 4:
            return int(tok)
    return None


def parse_monsters(dow):
    """From the cached survey, for each species use the most-recent survey that
    has a length histogram and count trophy fish (length >= threshold).
    -> {species: {'n': trophyCount, 'big': maxLengthInches, 'y': year}}"""
    path = os.path.join(CACHE_DIR, f"{dow}_survey.json")
    if not os.path.exists(path):
        return {}
    try:
        res = json.load(open(path)).get("result")
    except (ValueError, OSError):
        return {}
    if not isinstance(res, dict):
        return {}
    surveys = [s for s in (res.get("surveys") or []) if isinstance(s, dict)]
    surveys.sort(key=lambda s: _year_of(s.get("surveyDate")) or 0, reverse=True)
    out = {}
    for sv in surveys:
        year = _year_of(sv.get("surveyDate"))
        lengths = sv.get("lengths") or {}
        if not isinstance(lengths, dict):
            continue
        for code, info in lengths.items():
            sp = SPCODE.get(code)
            if not sp or sp in out or not isinstance(info, dict):
                continue
            fc = info.get("fishCount") or []
            pairs = [(ln, c) for ln, c in fc
                     if isinstance(ln, (int, float)) and isinstance(c, (int, float))]
            if not pairs:
                continue
            thr = MONSTER_LEN[sp]
            n = sum(c for ln, c in pairs if ln >= thr)
            big = max(ln for ln, c in pairs)
            out[sp] = {"n": int(n), "big": int(big), "y": year}
    return out


# --------------------------------------------------------------------------- #
# Boat / water access  (from cached survey JSON — no network)
# --------------------------------------------------------------------------- #
def parse_access(dow):
    """Return access dict, or None if we have no survey record to judge from."""
    path = os.path.join(CACHE_DIR, f"{dow}_survey.json")
    if not os.path.exists(path):
        return None                       # unknown -> caller leaves it unset
    try:
        res = json.load(open(path)).get("result")
    except (ValueError, OSError):
        return None
    if not isinstance(res, dict) or "accesses" not in res:
        return None                       # survey exists but no access field
    rows = res.get("accesses") or []
    rows = [a for a in rows if isinstance(a, dict)]
    items, owners = [], []
    for a in rows:
        owner = OWNER.get((a.get("ownerTypeId") or "").strip(), None)
        comment = (a.get("lakeAccessComments") or "").strip()
        item = {}
        if owner:
            item["owner"] = owner; owners.append(owner)
        if comment:
            item["note"] = comment
        if item:
            items.append(item)
    return {"has": len(rows) > 0,
            "n": len(rows),
            "owners": sorted(set(owners)),
            "list": items[:4]}


# --------------------------------------------------------------------------- #
# Stocking  (scraped HTML table, cached)
# --------------------------------------------------------------------------- #
def fetch_stocking_html(dow, sleep=0.3, force=False):
    path = os.path.join(CACHE_DIR, f"{dow}_stocking.html")
    if os.path.exists(path) and not force:
        return open(path, encoding="utf-8", errors="replace").read()
    url = STOCK_URL.format(dow=dow)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lake-survey-puller/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode("utf-8", "replace")
            open(path, "w", encoding="utf-8").write(txt)
            time.sleep(sleep)
            return txt
        except (urllib.error.URLError, TimeoutError) as e:
            time.sleep(1.5 * (attempt + 1))
    print(f"  stocking FAILED {dow}", file=sys.stderr)
    return None


_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _cells(row):
    return [html.unescape(_TAG.sub("", c)).strip() for c in _CELL.findall(row)]


def parse_stocking(htmltext):
    """-> {'recent':[{y,sp,size,n}], 'lastYear':int|None, 'species':{sp:year}}"""
    empty = {"recent": [], "lastYear": None, "species": {}}
    if not htmltext:
        return empty
    for tbl in _TABLE.findall(htmltext):
        rows = [_cells(r) for r in _ROW.findall(tbl)]
        rows = [c for c in rows if c]
        if not rows:
            continue
        header = [h.lower() for h in rows[0]]
        if not ("year" in header and "species" in header):
            continue
        idx = {h: i for i, h in enumerate(header)}
        iy, isp = idx["year"], idx["species"]
        isz = idx.get("size"); inu = idx.get("number")
        recs = []
        for c in rows[1:]:
            if len(c) <= iy:
                continue
            ym = re.search(r"\b(19|20)\d{2}\b", c[iy])
            if not ym:
                continue
            year = int(ym.group())
            sp = c[isp] if isp < len(c) else ""
            sp = re.sub(r"\s*\d+\s*$", "", sp).strip()   # drop footnote markers (e.g. "Walleye2")
            size = c[isz] if (isz is not None and isz < len(c)) else ""
            num = None
            if inu is not None and inu < len(c):
                m = re.search(r"[\d,]+", c[inu])
                if m:
                    num = int(m.group().replace(",", ""))
            recs.append({"y": year, "sp": sp, "size": size, "n": num})
        if not recs:
            return empty
        recs.sort(key=lambda r: r["y"], reverse=True)
        species = {}
        for r in recs:
            if r["sp"] and r["sp"] not in species:
                species[r["sp"]] = r["y"]
        recent = [r for r in recs if r["y"] >= SINCE_YEAR][:MAX_RECENT]
        if not recent:                      # keep at least the single latest
            recent = recs[:1]
        return {"recent": recent,
                "lastYear": recs[0]["y"],
                "species": species}
    return empty


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/full_lakes.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--no-stocking", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-fetch stocking pages")
    args = ap.parse_args()

    lakes = json.load(open(args.file))
    work = lakes[:args.limit] if args.limit else lakes
    n = len(work)
    acc_hits = stk_hits = 0
    mon_hits = 0
    for i, lake in enumerate(work, 1):
        dow = lake["id"]
        access = parse_access(dow)
        if access is not None:
            lake["access"] = access
            if access["has"]:
                acc_hits += 1
        # trophy ("monster") counts from length histograms (cache only, no network)
        mons = parse_monsters(dow)
        for f in lake.get("fish", []):
            m = mons.get(f["species"])
            if m:
                f["monN"] = m["n"]
                f["bigLen"] = m["big"]
                if m["n"] > 0:
                    mon_hits += 1
            else:
                f.pop("monN", None); f.pop("bigLen", None)
        if not args.no_stocking:
            stk = parse_stocking(fetch_stocking_html(dow, sleep=args.sleep, force=args.force))
            if stk["recent"]:
                lake["stocking"] = stk
                stk_hits += 1
            else:
                lake.pop("stocking", None)
        if i % 50 == 0 or i == n:
            print(f"  {i}/{n}  access:{acc_hits} stocked:{stk_hits}", file=sys.stderr)

    if not args.limit:
        json.dump(lakes, open(args.file, "w"), separators=(",", ":"))
        print(f"Wrote {args.file}: {acc_hits} lakes w/ access, {stk_hits} w/ recent stocking, "
              f"{mon_hits} species-records with >=1 trophy fish.")
    else:
        print(f"(dry --limit run) access:{acc_hits} stocked:{stk_hits} of {n}")
        print(json.dumps({k: work[0].get(k) for k in ("id", "name", "access", "stocking")}, indent=2)[:1200])


if __name__ == "__main__":
    main()
