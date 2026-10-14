# NYC Open Data: 311 complaints per building, Centennial Properties vs. everyone else

`centennial_311.py` uses NYC Open Data to compare how many 311 complaints Centennial Properties NY buildings get compared with all other buildings registered with HPD.

## Method

1. **Centennial buildings.** HPD Registration Contacts (`feu5-w2e2`): registrations whose managing agent is "Centennial Properties NY". The match also catches spelling variants (`CENTENNIAL PROPERTY NY INC`, `CENTENIAL PROPERTIES`, …). Every one of these contacts uses 424 W 51st St, the office of Steven Croman's portfolio. "Centennial Restorations" and "Centennial Astoria" are unrelated owners and are excluded.
2. **Building universe.** HPD Multiple Dwelling Registrations (`tesw-yqqr`), limited to registrations valid at some point in the 311 window, collapsed to one row per BBL (tax lot). The script then keeps only lots that have residential units in PLUTO (`64uk-42ks`).
3. **311 complaints.** 311 Service Requests (`erm2-nwe9`), counted per BBL on the server. It counts all complaints, plus a separate count of those routed to HPD (heat/hot water, plumbing, paint, pests, etc.).
4. **Window.** 2025-10-01 to 2026-09-30, the last 12 full months.

## Results (12 months ending 2026-09-30)

| | Centennial | Non-Centennial (all) | Non-Centennial, matched* |
|---|---:|---:|---:|
| Buildings | 119 | 134,331 | — |
| **Avg 311 complaints / building** | **35.7** | 13.9 | 15.4 |
| Median 311 complaints / building | 20 | 2 | — |
| Avg HPD 311 complaints / building | 23.4 | 6.0 | 6.6 |
| Avg residential units | 17.0 | 18.0 | — |
| 311 complaints / unit | 2.10 | 0.77 | — |
| HPD 311 complaints / unit | 1.38 | 0.33 | — |

\* Non-Centennial buildings reweighted to match Centennial's mix of borough and building size (unit-count buckets). Centennial's buildings are almost all in Manhattan, where complaint rates run higher.

**Takeaway.** The average Centennial building got about 36 complaints in the year, versus about 14 for other HPD-registered buildings (about 15 after matching on borough and size). That's roughly 2.3–2.6× as many. The gap is largest for housing-condition complaints routed to HPD: about 23 per building versus about 6.6, or about 3.5×. The median gap (20 vs 2) shows this isn't driven by a few outliers.

The per-building results are in `centennial_buildings_311.csv`. The top three are 326 E 100th St (431 complaints), 1349 Amsterdam Ave (378), and 358 W 51st St (170).

## Caveats

- Portfolio membership comes from HPD's managing-agent field. Croman buildings that list a different agent are missed.
- 311 complaints are tied to buildings by geocoded BBL. Complaints with no BBL (about 12% of the total) are dropped.
- 311 volume reflects how often tenants report problems as well as how many problems there are.

## Run

```
pip install pandas requests
python3 centennial_311.py --start 2025-10-01 --end 2026-10-01
```
Raw pulls are cached in `data/` (git-ignored).
