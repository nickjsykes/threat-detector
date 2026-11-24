# Threat Detector

Ancient woodland can't be replaced. Once it's gone, it's gone. Much of the damage
starts with an ordinary planning application: an extension built into the root
zone, a housing estate on the wood's edge, a poultry unit whose ammonia drifts
into the trees. If nobody objects inside the consultation window, nobody objects.

Threat Detector reads every week's planning applications from three councils and
checks each one against Natural England's Ancient Woodland Inventory and the
Woodland Trust's Ancient Tree Inventory. It flags the ones that need a human to
look, says why, and writes a report. Volunteers then spend their evening on the
handful of applications that matter, not the hundreds that don't.

It has screened every weekly list across three planning authorities since
mid-2026, for ancient woodland planning-watch volunteering.
This repo was extracted from the private project it has run in every week since; the
history before November 2026 lives there.

## What it does each week

1. **Fetches the weekly lists.** Westmorland & Furness (South Lakeland area)
   publishes a PDF. South Downs National Park Authority and New Forest District
   Council run Idox Public Access portals. It reads both kinds.
2. **Sorts the applications.** Each proposal is read for what kind of development
   it is. That sets a buffer: 15 m for an extension or a single house, 30 m for
   20 to 99 homes, 50 m for 100+ homes, leisure, utilities or slurry pits, 100 m for
   quarries, 5 km for intensive poultry and pig units. Listed building consent,
   most discharges of conditions, tree works for safety or conservation, and
   anything already decided are set aside, each with a reason.
3. **Places each site as precisely as the council allows.** The red-line
   boundary from the council's own map where it publishes one. Otherwise the
   building's UPRN address point, then the postcode centre.
4. **Measures.** Exact distance from the site to every ancient wood within
   500 m (or the buffer, if bigger), and to every recorded ancient or veteran
   tree. Pure Python geometry in British National Grid, no GIS install needed.
5. **Flags and explains.** Inside the buffer: flagged, with a plain sentence on
   the risk. Just outside it: a near miss, with a wider margin when the site was
   only placed by postcode. Couldn't be placed at all: listed for a manual check,
   never silently dropped.
6. **Writes a report** per council per week: `report.md` for people,
   `screening.json` for anything else.

It is built to fail loudly. If the woodland or tree service doesn't answer, the
week is not saved and the next run tries again. A failed lookup must never read
as "nothing nearby". A council that can't be reached makes the run exit with an
error, so a scheduler notices.

## Who it's for

Anyone who watches planning applications for ancient woodland and veteran trees:
volunteers, Wildlife Trusts, tree wardens, parish councils. The three councils
here are the ones it runs on, and any other Idox council takes one line of config.

## Install

Python 3.10 or newer.

```
pip install -r requirements.txt
```

## Run it

Screen the latest week for every council in `threatdetector/councils.json`:

```
python -m threatdetector run --out reports
```

One council, a past week, or a re-run:

```
python -m threatdetector run --council southdowns --week "13 Jul 2026" --out reports
python -m threatdetector run --council wandf --force --out reports
```

Reports land in `reports/<council>/<week>/`. The tool remembers the last week it
screened for each council (`reports/state.json`), so it's safe to run every day
from cron or Task Scheduler. Exit code 0 = screened something new, 2 = nothing new
yet, 1 = something failed.

### Screen your own list

Got applications from somewhere else? Put them in a CSV with columns
`app_no,address,proposal,easting,northing` (British National Grid; `status` and
`url` optional) and run:

```
python -m threatdetector screen my_list.csv --out reports
```

### Work offline

Pass local GeoJSON files (EPSG:27700) instead of the live services:

```
python -m threatdetector screen tests/fixtures/applications.csv \
    --woodland tests/fixtures/woodland.geojson --trees tests/fixtures/trees.geojson --out reports
```

Natural England publishes the full Ancient Woodland Inventory as open data.

### Better placement for Idox councils (optional)

Idox lists give addresses, not coordinates. With a local copy of OS Open UPRN, sites
without a red line are placed at the building rather than the postcode centre:

```
python tools/build_uprn_db.py osopenuprn_YYYYMM_csv.zip uprn.sqlite
python -m threatdetector run --uprn-db uprn.sqlite --out reports
```

## Add a council

Any council on Idox Public Access: add an entry to `threatdetector/councils.json`.

```json
{
  "id": "mycouncil",
  "name": "My District Council",
  "type": "idox",
  "base_url": "https://planning.mycouncil.gov.uk/online-applications",
  "red_lines": false
}
```

Set `red_lines` to `true` if the portal has a map tab. Some council portals send
an incomplete TLS certificate chain. `"verify_tls": false` works round that for
that council only.

## Tests

```
pip install pytest
python -m pytest
```

Everything in the tests is synthetic: made-up woods, trees, addresses and
application numbers. No network needed.

## Data

- Ancient Woodland Inventory (England): Natural England, Open Government Licence.
- Ancient Tree Inventory: Woodland Trust. Queried through the public ATI map.
- Postcode lookup: postcodes.io (Open Government Licence data).
- OS Open UPRN: Ordnance Survey, Open Government Licence.
- Planning lists: published by each council.

A flag means "look at this", not "object to this". The site plan, the officer's
report and local knowledge decide that.

## Licence

GPL-3.0. Use it, change it, run it for your own group freely. If you share a changed version, share its code under the same licence.
