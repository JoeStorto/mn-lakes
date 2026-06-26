#!/usr/bin/env python3
"""
mn_lakefinder_pull.py
=====================
Build the fish map's `data/full_lakes.json` straight from the live Minnesota DNR
LakeFinder service.

Each lake needs TWO calls (this is how the DNR's own site works):

  1) METADATA  (name, county, depth, area, coordinates)
     http://services.dnr.state.mn.us/api/lakefinder/by_id/v1?id=<DOW>
     -> results[0].{name, county, morphology{max_depth,mean_depth,area,
        littoral_area}, point{epsg:4326:[lon,lat]}}

  2) SURVEYS  (catch rates, weights, lengths, by survey date)
     https://maps.dnr.state.mn.us/cgi-bin/lakefinder/detail.cgi?type=lake_survey&id=<DOW>
     -> result.surveys[].{surveyDate, fishCatchSummaries[]{species,gear,CPUE,
        totalCatch,averageWeight,quartileCount}, lengths{CODE:{fishCount:[[in,n]]}}}

USAGE
-----
Inspect one lake (sanity check):
    python3 mn_lakefinder_pull.py inspect 11020300

Bulk build the map data file:
    python3 mn_lakefinder_pull.py pull --dows dows.txt --out data/full_lakes.json \
        --since 2015 --recent 2019

Options:
    --since   earliest survey year for trend lines        (default 2015)
    --recent  earliest year counted as "current"          (default 2019)
    --species comma list of display names                 (default: the 5 sport fish)
    --sleep   seconds between lakes                        (default 0.5)

Standard library only (urllib). Raw responses cache to raw_cache/ so re-runs are
fast. The DNR retains copyright on lake data — review their Data & Software
License Agreement before any public/commercial use.
"""

import argparse, json, os, sys, time, urllib.request, urllib.error

META_API   = "http://services.dnr.state.mn.us/api/lakefinder/by_id/v1?id={dow}"
SURVEY_API = "https://maps.dnr.state.mn.us/cgi-bin/lakefinder/detail.cgi?type=lake_survey&id={dow}"
CACHE_DIR  = "raw_cache"

# DNR species code -> (display name, preferred gear keyword, size metric)
SPECIES = {
    "WAE": ("Walleye",         "gill", "weight"),
    "MUE": ("Muskie",          "gill", "length"),
    "NOP": ("Northern Pike",   "gill", "length"),
    "LMB": ("Largemouth Bass", "trap", "weight"),
    "SMB": ("Smallmouth Bass", "trap", "weight"),
}
DISPLAY_TO_CODE = {v[0]: k for k, v in SPECIES.items()}
LEN_CODES = {"NOP", "MUE"}

LBINS  = [(0,5),(6,7),(8,9),(10,11),(12,14),(15,19),(20,24),(25,29),
          (31,34),(35,39),(40,44),(45,49),(50,99)]
LMIDS  = [2.5,6.5,8.5,10.5,13,17,22,27,32.5,37,42,47,52]
LLABELS= ["0-5","6-7","8-9","10-11","12-14","15-19","20-24","25-29",
          "31-34","35-39","40-44","45-49","50+"]


# ---------------------------------------------------------------------------
# Fetch (cached)
# ---------------------------------------------------------------------------
def _fetch(url, cache_path, sleep, force=False):
    if os.path.exists(cache_path) and not force:
        try:
            return json.load(open(cache_path))
        except json.JSONDecodeError:
            pass
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lake-survey-puller/2.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            json.dump(data, open(cache_path, "w"))
            time.sleep(sleep)
            return data
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            wait = 2 * (attempt + 1)
            print(f"    retry ({e}) in {wait}s", file=sys.stderr)
            time.sleep(wait)
    return None

def fetch_meta(dow, sleep=0.5, force=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return _fetch(META_API.format(dow=dow), os.path.join(CACHE_DIR, f"{dow}_meta.json"), sleep, force)

def fetch_survey(dow, sleep=0.5, force=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return _fetch(SURVEY_API.format(dow=dow), os.path.join(CACHE_DIR, f"{dow}_survey.json"), sleep, force)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def num(v):
    try:
        if v in (None, "", "NA"):
            return None
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None

def parse_quartile(q):
    """'0.9-4.3' -> [0.9, 4.3] (the DNR normal range for that lake class)."""
    if not isinstance(q, str) or "-" not in q:
        return None
    try:
        lo, hi = q.split("-", 1)
        return [float(lo.strip()), float(hi.strip())]
    except ValueError:
        return None

def slope(pairs):
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    n = len(xs); sx = sum(xs); sy = sum(ys)
    sxx = sum(x*x for x in xs); sxy = sum(x*y for x, y in zip(xs, ys))
    den = n*sxx - sx*sx
    return None if den == 0 else round((n*sxy - sx*sy)/den, 4)

def year_of(date_str):
    for tok in str(date_str).replace("/", "-").split("-"):
        if tok.isdigit() and len(tok) == 4:
            return int(tok)
    return None


# ---------------------------------------------------------------------------
# Parse metadata + surveys into one map-ready lake record
# ---------------------------------------------------------------------------
def parse_meta(dow, meta):
    if not meta or str(meta.get("status", "")).upper() == "ERROR":
        return None
    res = meta.get("results")
    res = res[0] if isinstance(res, list) and res else res
    if not isinstance(res, dict):
        return None
    m = res.get("morphology") or {}
    pt = (res.get("point") or {}).get("epsg:4326") or [None, None]
    area = num(m.get("area"))
    lit = num(m.get("littoral_area"))
    return {
        "id": dow,
        "name": str(res.get("name") or dow).title(),
        "county": str(res.get("county") or "").title(),
        "lat": round(pt[1], 5) if pt[1] is not None else None,
        "lon": round(pt[0], 5) if pt[0] is not None else None,
        "area": int(round(area)) if area else None,
        "maxDepth": (lambda v: int(round(v)) if v else None)(num(m.get("max_depth"))),
        "meanDepth": (lambda v: round(v, 1) if v else None)(num(m.get("mean_depth"))),
        "littoralPct": int(round(min(100, lit/area*100))) if (lit and area) else None,
        # lakeClass isn't in this feed; left out (map treats it as optional)
    }

def length_stats(lengths_block, code):
    """Sum a species' [inch,count] pairs into the map's inch-bins."""
    if not isinstance(lengths_block, dict):
        return None
    entry = lengths_block.get(code)
    if not entry:
        return None
    fc = entry.get("fishCount") or []
    counts = [0]*len(LBINS)
    for pair in fc:
        try:
            inch, qty = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        for i, (lo, hi) in enumerate(LBINS):
            if lo <= inch <= hi:
                counts[i] += qty; break
    tot = sum(counts)
    if tot < 1:
        return None
    mean = sum(c*mid for c, mid in zip(counts, LMIDS)) / tot
    mx = next((LLABELS[i] for i in range(len(counts)-1, -1, -1) if counts[i] >= 1), None)
    return {"lenMean": round(mean, 1), "lenMax": mx,
            "bins": [int(c) for c in counts], "n": int(tot)}

def parse_surveys(dow, survey, area, since, recent, want_codes):
    if not survey:
        return []
    res = survey.get("result") or {}
    surveys = res.get("surveys") or []
    sqmi = area/640 if area else None

    per = {}   # code -> {year: {...}}
    for sv in surveys:
        yr = year_of(sv.get("surveyDate"))
        if not yr or yr < since:
            continue
        rows = sv.get("fishCatchSummaries") or []
        lengths = sv.get("lengths") or {}
        for row in rows:
            code = str(row.get("species") or "").strip().upper()
            if code not in want_codes:
                continue
            cpue = num(row.get("CPUE"))
            if cpue is None or cpue <= 0:
                continue
            gear = str(row.get("gear") or "").lower()
            bucket = per.setdefault(code, {}).setdefault(yr, {"rows": []})
            bucket["rows"].append({
                "gear": gear, "cpue": cpue,
                "total": num(row.get("totalCatch")),
                "weight": num(row.get("averageWeight")),
                "normal": parse_quartile(row.get("quartileCount")),
            })
        for code in (want_codes & LEN_CODES):
            ls = length_stats(lengths, code)
            if ls and code in per and yr in per[code]:
                per[code][yr].update(ls)
            elif ls:
                per.setdefault(code, {}).setdefault(yr, {"rows": []}).update(ls)

    fish = []
    for code, years in per.items():
        disp, pref, _ = SPECIES[code]
        yearly = []
        for y, info in sorted(years.items()):
            rows = info.get("rows", [])
            gr = [r for r in rows if pref in r["gear"]] or rows
            if not gr:
                continue
            cp = sum(r["cpue"] for r in gr) / len(gr)
            yearly.append((y, round(cp, 2), gr, info))
        if not yearly:
            continue
        nr = {"species": disp, "gear": "gn" if pref == "gill" else "tn"}
        series = [(y, cp) for (y, cp, _, _) in yearly]
        if len(series) >= 2:
            nr["trendSlope"] = slope(series)
            nr["trendSeries"] = [{"y": y, "v": v} for y, v in series]
        recents = [t for t in yearly if t[0] >= recent]
        if recents:
            y, cp, gr, info = recents[-1]
            tot = sum(r["total"] for r in gr if r["total"]) or None
            wts = [r["weight"] for r in gr if r["weight"]]
            norms = [r["normal"] for r in gr if r["normal"]]
            nr["rYear"] = y
            if wts:
                nr["rWeight"] = round(sum(wts)/len(wts), 2)
            if tot and sqmi:
                nr["rDensity"] = round(tot/sqmi, 1)
            if norms:
                nr["normal"] = norms[0]
            if code in LEN_CODES and info.get("lenMean"):
                nr.update({k: info[k] for k in ("lenMean", "lenMax", "bins", "n") if k in info})
        fish.append(nr)
    return fish


def build_lake(dow, since, recent, want_codes, sleep):
    meta = parse_meta(dow, fetch_meta(dow, sleep))
    if not meta:
        return None
    fish = parse_surveys(dow, fetch_survey(dow, sleep), meta["area"], since, recent, want_codes)
    if not fish:
        return None
    keep = any(("rDensity" in f) or ("trendSlope" in f) or f.get("rWeight") or f.get("lenMean")
               for f in fish)
    if not keep:
        return None
    order = ["Walleye", "Muskie", "Northern Pike", "Largemouth Bass", "Smallmouth Bass"]
    fish.sort(key=lambda x: order.index(x["species"]) if x["species"] in order else 99)
    meta["fish"] = fish
    return meta


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_inspect(args):
    dow = args.dow
    meta = fetch_meta(dow, force=True)
    sur = fetch_survey(dow, force=True)
    print(f"\n=== {dow} ===")
    res = (meta or {}).get("results"); res = res[0] if isinstance(res, list) and res else res
    print("META ok:", bool(res), "| name:", (res or {}).get("name"),
          "| morphology:", (res or {}).get("morphology"))
    svs = ((sur or {}).get("result") or {}).get("surveys") or []
    print("SURVEYS:", len(svs))
    if svs:
        latest = max(svs, key=lambda s: year_of(s.get("surveyDate")) or 0)
        print("latest survey:", latest.get("surveyDate"),
              "| catch rows:", len(latest.get("fishCatchSummaries") or []))
    lk = build_lake(dow, args.since, args.recent, set(SPECIES), 0)
    print("\nPARSED ->")
    print(json.dumps(lk, indent=1)[:1600] if lk else "no target-species data")

def cmd_pull(args):
    if args.dows and os.path.exists(args.dows):
        dows = [ln.strip() for ln in open(args.dows) if ln.strip()]
    elif os.path.exists("lake_dows.json"):
        dows = [d["id"] for d in json.load(open("lake_dows.json"))]
    else:
        print("Provide --dows (one DOW per line) or lake_dows.json"); return

    if args.species:
        names = [s.strip() for s in args.species.split(",")]
        want = {DISPLAY_TO_CODE[n] for n in names if n in DISPLAY_TO_CODE}
    else:
        want = set(SPECIES)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out = []
    for i, dow in enumerate(dows, 1):
        lake = build_lake(dow, args.since, args.recent, want, args.sleep)
        if lake:
            out.append(lake)
        if i % 25 == 0 or i == len(dows):
            print(f"  {i}/{len(dows)}  kept {len(out)}", file=sys.stderr)
    out.sort(key=lambda x: -(x.get("area") or 0))
    json.dump(out, open(args.out, "w"), separators=(",", ":"))
    print(f"\nWrote {args.out} with {len(out)} lakes.")

def main():
    ap = argparse.ArgumentParser(description="Pull MN DNR lake surveys -> data/full_lakes.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("inspect"); pi.add_argument("dow")
    pi.add_argument("--since", type=int, default=2015); pi.add_argument("--recent", type=int, default=2019)
    pi.set_defaults(func=cmd_inspect)
    pp = sub.add_parser("pull")
    pp.add_argument("--dows", default="dows.txt")
    pp.add_argument("--out", default="data/full_lakes.json")
    pp.add_argument("--since", type=int, default=2015)
    pp.add_argument("--recent", type=int, default=2019)
    pp.add_argument("--species", default="")
    pp.add_argument("--sleep", type=float, default=0.5)
    pp.set_defaults(func=cmd_pull)
    args = ap.parse_args(); args.func(args)

if __name__ == "__main__":
    main()