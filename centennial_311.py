"""Compare 311 complaints per building: Centennial Properties vs. all other buildings.

Data (NYC Open Data, Socrata):
  - HPD Registration Contacts (feu5-w2e2): find registrations whose managing agent
    is "Centennial Properties NY" (and spelling variants).
  - HPD Multiple Dwelling Registrations (tesw-yqqr): registration -> building (BBL/BIN).
  - 311 Service Requests (erm2-nwe9): complaint counts grouped by BBL.
  - PLUTO (64uk-42ks): residential units per BBL, used for per-unit rates and size matching.

Universe: every HPD-registered building whose registration was valid during the 311 window
(registration end date on or after the window start).

Usage: python3 centennial_311.py [--start 2025-10-01] [--end 2026-10-01]
"""

import argparse
import os
import re
import time

import pandas as pd
import requests

BASE = "https://data.cityofnewyork.us/resource"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PAGE = 50000

# Matches "CENTENNIAL PROPERTIES NY", "CENTENIAL PROPERTIE NY", "CENTENNIALPROPERTIENY", ...
# but not "CENTENNIAL RESTORATIONS" or "Centennial Astoria" (unrelated owners).
CENTENNIAL_RE = re.compile(r"CENTEN+IAL\s*PROP", re.I)


def soql(dataset, cache_name, **params):
    """Fetch every row of a SoQL query, paging, with a CSV cache in ./data."""
    path = os.path.join(CACHE, cache_name)
    if os.path.exists(path):
        return pd.read_csv(path, dtype=str)
    rows, offset = [], 0
    while True:
        for attempt in range(5):
            try:
                page = requests.get(
                    f"{BASE}/{dataset}.json",
                    params={**params, "$limit": PAGE, "$offset": offset},
                    timeout=600,
                )
                page.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(2**attempt)
        batch = page.json()
        rows.extend(batch)
        print(f"  {dataset}: {len(rows):,} rows", flush=True)
        if len(batch) < PAGE:
            break
        offset += PAGE
    os.makedirs(CACHE, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


def make_bbl(boro, block, lot):
    return (
        boro.astype(str)
        + block.astype(int).astype(str).str.zfill(5)
        + lot.astype(int).astype(str).str.zfill(4)
    )


def load_buildings(as_of):
    regs = soql(
        "tesw-yqqr",
        "hpd_registrations.csv",
        **{
            "$select": "registrationid,buildingid,boroid,boro,housenumber,streetname,"
            "block,lot,bin,registrationenddate",
            "$order": "registrationid",
        },
    )
    regs = regs.dropna(subset=["boroid", "block", "lot"])
    regs = regs[pd.to_datetime(regs["registrationenddate"]) >= as_of].copy()
    regs["bbl"] = make_bbl(regs["boroid"], regs["block"], regs["lot"])

    contacts = soql(
        "feu5-w2e2",
        "centennial_contacts.csv",
        **{
            "$where": "upper(corporationname) like '%CENTEN%'",
            "$order": "registrationcontactid",
        },
    )
    is_cent = contacts["corporationname"].str.contains(CENTENNIAL_RE, na=False)
    cent_regs = set(contacts.loc[is_cent, "registrationid"])
    regs["centennial"] = regs["registrationid"].isin(cent_regs)

    # One row per BBL (a lot can hold several registered buildings; 311 is geocoded to BBL).
    return (
        regs.groupby("bbl")
        .agg(
            boro=("boro", "first"),
            address=("housenumber", "first"),
            street=("streetname", "first"),
            centennial=("centennial", "max"),
        )
        .reset_index()
    )


def load_311(start, end):
    where = f"created_date >= '{start}' AND created_date < '{end}' AND bbl IS NOT NULL"
    total = soql(
        "erm2-nwe9",
        f"311_by_bbl_{start}_{end}.csv",
        **{"$select": "bbl, count(*) AS n", "$where": where, "$group": "bbl", "$order": "bbl"},
    )
    hpd = soql(
        "erm2-nwe9",
        f"311_hpd_by_bbl_{start}_{end}.csv",
        **{
            "$select": "bbl, count(*) AS n",
            "$where": where + " AND agency = 'HPD'",
            "$group": "bbl",
            "$order": "bbl",
        },
    )
    out = total.rename(columns={"n": "complaints"}).merge(
        hpd.rename(columns={"n": "hpd_complaints"}), on="bbl", how="left"
    )
    for col in ("complaints", "hpd_complaints"):
        out[col] = pd.to_numeric(out[col]).fillna(0).astype(int)
    return out


def load_units():
    pluto = soql(
        "64uk-42ks",
        "pluto_units.csv",
        **{"$select": "bbl,unitsres", "$where": "unitsres > 0", "$order": "bbl"},
    )
    pluto["bbl"] = pluto["bbl"].str.split(".").str[0]
    pluto["unitsres"] = pd.to_numeric(pluto["unitsres"])
    return pluto


def summarize(df, label):
    g = df.groupby("centennial")
    out = pd.DataFrame(
        {
            "buildings": g.size(),
            "avg_311_per_bldg": g["complaints"].mean(),
            "median_311_per_bldg": g["complaints"].median(),
            "avg_hpd_311_per_bldg": g["hpd_complaints"].mean(),
            "avg_units": g["unitsres"].mean(),
            "311_per_unit": g["complaints"].sum() / g["unitsres"].sum(),
            "hpd_311_per_unit": g["hpd_complaints"].sum() / g["unitsres"].sum(),
        }
    ).rename(index={True: "Centennial", False: "Non-Centennial"})
    print(f"\n=== {label} ===")
    print(out.round(2).T.to_string())
    return out


def matched_comparison(df):
    """Reweight non-Centennial buildings to Centennial's borough x size mix."""
    bins = [0, 5, 10, 20, 50, 100, 10**6]
    df = df.assign(size=pd.cut(df["unitsres"], bins))
    cent = df[df["centennial"]]
    mix = cent.groupby(["boro", "size"], observed=True).size().rename("w")
    other = df[~df["centennial"]].groupby(["boro", "size"], observed=True)[
        ["complaints", "hpd_complaints"]
    ].mean()
    j = other.join(mix, how="inner")
    w = j["w"] / j["w"].sum()
    covered = j["w"].sum()
    print(f"\n=== Borough x size matched (covers {covered}/{len(cent)} Centennial bldgs) ===")
    print(f"  Centennial avg 311/bldg:            {cent['complaints'].mean():.2f}")
    print(f"  Matched non-Centennial avg 311/bldg:  {(j['complaints'] * w).sum():.2f}")
    print(f"  Centennial avg HPD 311/bldg:        {cent['hpd_complaints'].mean():.2f}")
    print(f"  Matched non-Centennial avg HPD 311:   {(j['hpd_complaints'] * w).sum():.2f}")
    return (j["complaints"] * w).sum(), (j["hpd_complaints"] * w).sum()


def by_complaint_type(df, start, end):
    """Avg complaints per building by 311 complaint type: Centennial vs. matched vs. all other."""
    where = f"created_date >= '{start}' AND created_date < '{end}' AND bbl IS NOT NULL"
    raw = soql(
        "erm2-nwe9",
        f"311_by_bbl_type_{start}_{end}.csv",
        **{
            "$select": "bbl, complaint_type, count(*) AS n",
            "$where": where,
            "$group": "bbl, complaint_type",
            "$order": "bbl, complaint_type",
        },
    )
    raw["n"] = pd.to_numeric(raw["n"])
    # 311 uses both "HEAT/HOT WATER" and "Heat/Hot Water"-style casing over time.
    raw["complaint_type"] = raw["complaint_type"].str.strip().str.upper()

    bins = [0, 5, 10, 20, 50, 100, 10**6]
    b = df[["bbl", "boro", "unitsres", "centennial"]].assign(size=pd.cut(df["unitsres"], bins))
    t = raw.merge(b, on="bbl", how="inner")

    cent = b[b["centennial"]]
    cent_avg = t[t["centennial"]].groupby("complaint_type")["n"].sum() / len(cent)
    other_b = b[~b["centennial"]]
    other_avg = t[~t["centennial"]].groupby("complaint_type")["n"].sum() / len(other_b)

    # Matched: per-stratum mean for other buildings, weighted by Centennial's stratum mix.
    keys = ["boro", "size"]
    stratum_n = other_b.groupby(keys, observed=True).size().rename("bldgs")
    mix = cent.groupby(keys, observed=True).size()
    w = (mix / mix.sum()).rename("w")
    s = t[~t["centennial"]].groupby(keys + ["complaint_type"], observed=True)["n"].sum().reset_index()
    s = s.merge(stratum_n.reset_index(), on=keys).merge(w.reset_index(), on=keys)
    s["contrib"] = s["n"] / s["bldgs"] * s["w"]
    matched_avg = s.groupby("complaint_type")["contrib"].sum()

    out = pd.DataFrame(
        {"centennial": cent_avg, "matched_other": matched_avg, "all_other": other_avg}
    ).fillna(0)
    out["ratio_vs_matched"] = out["centennial"] / out["matched_other"].where(out["matched_other"] > 0)
    out = out.sort_values("centennial", ascending=False)
    out.round(3).to_csv("complaint_types_per_building.csv", index_label="complaint_type")
    print("\n=== Avg complaints per building by type (top 15 for Centennial) ===")
    print(out.head(15).round(2).to_string())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-10-01")
    ap.add_argument("--end", default="2026-10-01")
    args = ap.parse_args()

    print("Loading HPD registrations and Centennial contacts...")
    bldgs = load_buildings(pd.Timestamp(args.start))
    print("Loading 311 counts by BBL...")
    c311 = load_311(args.start, args.end)
    print("Loading PLUTO units...")
    units = load_units()

    df = bldgs.merge(c311, on="bbl", how="left").merge(units, on="bbl", how="left")
    df[["complaints", "hpd_complaints"]] = df[["complaints", "hpd_complaints"]].fillna(0)
    df = df[df["unitsres"].notna()]  # keep residential lots PLUTO can size

    print(f"\n311 window: {args.start} to {args.end} (exclusive)")
    summarize(df, "All HPD-registered buildings (registration valid during window)")
    matched_comparison(df)
    by_complaint_type(df, args.start, args.end)

    top = df[df["centennial"]].sort_values("complaints", ascending=False)
    top[["bbl", "boro", "address", "street", "unitsres", "complaints", "hpd_complaints"]].to_csv(
        "centennial_buildings_311.csv", index=False
    )
    print("\nTop Centennial buildings by 311 complaints:")
    print(top.head(10)[["address", "street", "boro", "unitsres", "complaints", "hpd_complaints"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
