# World Bank corridor integration

Stage 1 passed Hugh's local QGIS **3.38.3-Grenoble** run: all 22 inputs verified,
all 143 project layers valid, and the original project file unchanged.
Stage 2 processing is implemented. Its first native Windows/QGIS run is still
required before results are integrated into the live website.

## Run Stage 2

1. Download [WB_STAGE2_FIXED.zip](https://github.com/hughlamarque-dev/wb-borderlands-environment/raw/refs/heads/main/tools/corridor-integration/WB_STAGE2_FIXED.zip) and choose **Extract all**.
2. Double-click **START_WB_STAGE2_FIXED.cmd** inside the extracted folder. QGIS opens the original project at
   C:\Users\hughl\OneDrive\Desktop\Public Maps\Public Experiment.qgz and starts
   processing. Keep that new QGIS window open. If the supplied GEMS data.csv
   is not found in Downloads or beside the project, select it in one file chooser.
3. When finished, the processed project and report folder open.
   Upload **WB_STAGE2_REPORT.zip** in the working conversation.

No separate Python installation, pip command or QGIS console entry is needed.
The launcher uses QGIS's own Python and the verified Stage 1 downloads. A
nonstandard QGIS installation may need one launcher-file selection. It changes
no execution policy and requires no administrator privileges.

It automatically fetches this additional data input:
[public baseline, 807,214 bytes](https://raw.githubusercontent.com/hughlamarque-dev/wb-borderlands-environment/main/tools/corridor-integration/baselines/2f10237a8e335e5acf049f801915e3153ac96c264ac03897bc2952b80d54b37f.json.gz).
It preserves the existing four boundaries, **9,500 grid cell IDs and geometries**,
public project-directory links and official World Bank metadata from repository
snapshot d4803fbd5e7a711808312a36d54d41ab69dffeb9. Its SHA-256 is its filename.
It contains no GEMS coordinates or event/raster values. Checksum-pinned parser
wheel URLs for supported Windows 64-bit Python versions are listed in
stage2_config.json. The matching parser is extracted into a task-local directory
without changing QGIS's installed packages.

Outputs go beside the original project under
WB_Corridor_Integration\runs_stage2\<run>:

- WB_Corridor_Stage2.gpkg;
- Public_Experiment_WB_Stage2.qgz, preserving existing layers;
- processed GeoJSONs and observation tables in web_outputs;
- WB_STAGE2_REPORT.zip, including processed review locations and observations.

The original project is never saved over. The ZIP excludes raw PBFs, the full
original GEMS CSV, project and GeoPackage. It is not uploaded automatically.
The launcher makes no website changes or GitHub login requests. Allow processing
time and working space; at least 2 GiB free is checked at startup, a starting guard
rather than a final peak-usage guarantee. Completed topology caches are checked
and reused. Interrupted incomplete caches are retained, not treated as complete.

## Run Stage 1

1. Download [START_WB_STAGE1.cmd](https://github.com/hughlamarque-dev/wb-borderlands-environment/raw/refs/heads/main/tools/corridor-integration/START_WB_STAGE1.cmd).
2. Double-click it. It locates installed QGIS, opens
   `C:\Users\hughl\OneDrive\Desktop\Public Maps\Public Experiment.qgz`,
   and displays a download progress panel. No separate Python installation or
   QGIS console commands are needed. A nonstandard QGIS installation may need
   one launcher-file selection.
3. Keep that new QGIS window open. When finished, its report folder opens.
   Upload **WB_STAGE1_REPORT.zip** in the working conversation for Stage 2.

The input directory is `WB_Corridor_Integration\inputs` beside the project.
Downloads require approximately **1.2 GB**, plus working space. This folder is
inside OneDrive because the project is there; OneDrive may also sync the inputs.
The script checks disk space before downloading. The later network-processing
stage will require additional space, assessed after local inspection.

If Windows or an organization security policy prevents the file from running,
report the exact message. Do not disable antivirus, administrator controls or
organization policies. This launcher changes no execution policy and requires
no administrator privileges. Stage 1 has passed its local Windows/QGIS run; Stage 2 needs its first native run.

## Direct downloads

These exact PBF files and their official MD5 sidecars were verified on
**9 October 2026**. All use the **8 October 2026** snapshot. The launcher downloads
them automatically; the links below provide the manual alternative.

| Country | Direct PBF | Bytes |
| --- | --- | ---: |
| Djibouti | [djibouti-261008.osm.pbf](https://download.geofabrik.de/africa/djibouti-261008.osm.pbf) | 7,025,642 |
| Ethiopia | [ethiopia-261008.osm.pbf](https://download.geofabrik.de/africa/ethiopia-261008.osm.pbf) | 139,883,558 |
| Kenya | [kenya-261008.osm.pbf](https://download.geofabrik.de/africa/kenya-261008.osm.pbf) | 351,308,754 |
| Somalia | [somalia-261008.osm.pbf](https://download.geofabrik.de/africa/somalia-261008.osm.pbf) | 164,730,769 |
| South Sudan | [south-sudan-261008.osm.pbf](https://download.geofabrik.de/africa/south-sudan-261008.osm.pbf) | 138,898,986 |
| Uganda | [uganda-261008.osm.pbf](https://download.geofabrik.de/africa/uganda-261008.osm.pbf) | 371,336,499 |

The current QGIS inventory contains several road shapefiles, which do not retain
the original node identities needed for defensible junction topology. The current
web road exports also omit several local street classes. The full PBFs retain road ways, their node identities and tags,
plus mapped settlements. They are inputs for later processing, not ready-made
connectivity layers. Do not replace their filenames with `latest`: a dated file
is checksum-pinned for reproducibility. Geofabrik retains dated extracts only
for a limited period; a later missing link must be replaced with a newly verified
snapshot and checksum, not silently substituted by the downloader.

### FEWS NET commodity data

The launcher also downloads monthly representations and unaggregated record
representations for these eight border-point IDs, retaining reporting-side and
unit distinctions. The monthly API URLs below are direct CSV downloads.
The launcher requests explicit columns, including the data-usage policy.

| Border point | ID | Direct monthly CSV | Review note |
| --- | ---: | --- | --- |
| Moyale | 143949 | [CSV](https://fdw.fews.net/api/tradeflowquantityvalue.csv?border_point=143949&schedule=Monthly) | Moyale/Borana candidate |
| Belet Hawo | 142977 | [CSV](https://fdw.fews.net/api/tradeflowquantityvalue.csv?border_point=142977&schedule=Monthly) | Shares catalogue coordinates with Bula Hawo |
| Bula Hawo | 257968 | [CSV](https://fdw.fews.net/api/tradeflowquantityvalue.csv?border_point=257968&schedule=Monthly) | Distinct livestock series, not another physical crossing |
| Nimule | 143953 | [CSV](https://fdw.fews.net/api/tradeflowquantityvalue.csv?border_point=143953&schedule=Monthly) | Karamoja candidate |
| Galafi | 143870 | [CSV](https://fdw.fews.net/api/tradeflowquantityvalue.csv?border_point=143870&schedule=Monthly) | Dikhil candidate |
| Loyado, Djibouti side | 143943 | [CSV](https://fdw.fews.net/api/tradeflowquantityvalue.csv?border_point=143943&schedule=Monthly) | Keep reporting-side identity |
| Loyado, Somalia side | 192531 | [CSV](https://fdw.fews.net/api/tradeflowquantityvalue.csv?border_point=192531&schedule=Monthly) | Keep reporting-side identity |
| Balho | 142975 | [CSV](https://fdw.fews.net/api/tradeflowquantityvalue.csv?border_point=142975&schedule=Monthly) | Historical input only; coordinates quarantined pending independent verification |

For each URL, `schedule=Daily` requests the API's unaggregated dated-record
representation. It does **not** establish that monitoring occurred daily.
Monthly bounds do not establish complete within-month observation coverage.
Missing observations are not zero trade. Commodity mass and livestock item
counts must remain separate. Import and export reporting must not be added
together as if they necessarily describe different shipments. Downloading a
series does not mean its location or values have been approved for publication.

FEWS exports are mutable. A receipt records the retrieval time, exact query,
SHA-256, reported units and published date coverage. A rerun reuses the existing
verified CSV snapshot rather than silently refreshing it. A later refresh should
use a new input folder or an explicit, reviewed refresh operation.

## What Stage 1 does

It inventories the current project's layers without reading their feature data,
redacts remote source URLs and connection strings, downloads source inputs,
checks PBF byte counts/MD5s and CSV schemas/crossing IDs, and writes receipts.
It hashes the project file before and after downloads to detect an external
change during that period. That comparison is a file-change check, not proof
that all original project layers are correct.

Verified downloads are reused. Interrupted PBFs can resume. Interrupted CSV
responses restart because their source is mutable. Unknown files and checksum
failures are preserved and reported rather than overwritten. The Stop button
keeps verified and partial downloads. A blocking network read may take up to
60 seconds to notice cancellation. Closing QGIS stops its background worker;
the next run can recover a tracked PBF partial.

The report ZIP contains inventory and receipts, **not** the large input files,
GEMS records, credentials, or QGIS project itself. It is not uploaded automatically.
The original project is never saved by the script, no new data layers are added,
and no GitHub login or publication occurs on the user's computer in Stage 1.

## 10 October: boundary precision fix

The first native run completed the six-extract topology cache (636,594 unique
road ways) and GEMS/FEWS table processing, then stopped during boundary repair.
The repair helper serialized its native GEOS result at 12 decimal places and
rebuilt it from WKT. Tiny components can collapse in that round trip and become
invalid again. The helper now filters polygon components directly in QGIS's
native geometry objects. Captured boundaries and repaired cell exports retain
17 decimal places. Source data and the existing topology cache identity are
preserved; the completed cache is reused on the next run.

A regression fixture reproduces the old invalid-after-repair failure and passes
the revised helper. Checks also cover mixed polygon/line collections and all
9,500 published cells plus boundary parts. The full native map-processing run
still requires the next local report. Optional independent regression tests use
Shapely; it is not a launcher dependency or installed into the user's QGIS.

## Stage 2 definitions

This first implementation counts shared OSM junction **nodes** on all motorway,
trunk, primary and associated link ways in the focus areas. It does not select
a named corridor spine or merge functional interchanges. A junction needs a
shared original node ID, at least three distinct statically accessible neighbours,
and incident major and local edges. Degree-two segmentation nodes and geometric
crossings without shared IDs are excluded. Overlapping country records are
deduplicated by OSM identity/version; same-version conflicts stop processing.

Grid outputs retain raw node counts and mapped major-road kilometres, node
counts per 10 km (with a minimum 0.5 km denominator), and the count/share that
reach OSM city/town nodes via local roads within **2, 5 and 10 km**. A separate
measure includes villages. Paths exclude all major edges and include the
straight-line place-to-local-road snap distance, limited to 250 m. Static motor
access, one-way directions and blocking node barriers are considered; conditional
access/direction roads are excluded. Turn restrictions, dynamic border closures
and actual surface passability are not modelled. A 15 km network halo is used.

Length uses a local AEQD metric CRS. Shared grid-boundary lengths have one
owner. Published rounding gaps are reported as unassigned road length or
null-cell junctions rather than forced into another cell. No-major-road cells,
undefined shares and small-denominator normalized values remain null.
Divided roads can contribute multiple junction nodes and carriageway lengths.
These are raw mapped node/road measures, not interchange or centreline counts.
OSM incompleteness and missing place nodes limit interpretation. These measures
do not establish territorial isolation, traffic, population access or economic
impact. GHSL 2030 is not used as a present-day settlement baseline.

Independent checks found small invalid geometries in four published boundaries
and 18 cells. GEOS linework repair is applied only to derived copies, preserving
polygon components and recording hashes and area changes. Changes over 0.1%
stop the run. Local/published boundary and published grid/boundary differences
over 0.1% also stop it. The baseline and source layers remain unchanged.
Repaired cells retain their published IDs.

Seven review layer types are exported per map: connectivity grid, junction nodes,
mapped places, major roads, GEMS locations, GEMS review locations and FEWS
reference places. Empty layers remain in GeoJSON reports and are omitted from
the GeoPackage. Junction styling distinguishes mapped town paths within 5 km.

## GEMS and FEWS integration

GEMS locations link by exact World Bank project ID to one canonical project and
existing directory/family records. Separate project IDs are retained when
coordinates coincide. The supplied file has 11,287 rows and 69 project IDs:
40 match focal directory entries, 24 context-only entries, and 5 are absent.
Country labels are not claimed to have been independently checked. Dates,
coordinate precision and implementation status were not supplied. A coordinate
is not evidence of a completed intervention; no project financing is allocated
to points. The suspicious P163980 marine-project location in Mandera is kept
in the review layer. Pre-2020 closing dates receive a historical review flag;
project status is not inferred from those dates. Distance to a major road is
straight-line distance, not network access or a causal relationship.

FEWS catalogue references identify five physical places: Moyale, Belet Hawo/Bula
Hawo, Nimule, Galafi and Loyado. Reporting sides and livestock series stay
separate. Balho observations are retained in the table while its coordinates
are quarantined. Published public monthly observations retain identities,
sources, commodities, flows and units. Recognized mass is converted to tonnes;
item and volume units remain separate. Missing quantities are not zero.
Rows labelled No Data are excluded even when their source value says zero.
Raw published record counts are not days monitored or completeness percentages.
No totals across reporting sides/series or route-level traffic are inferred.

## Stage 3 and maintenance

After reviewing the actual Stage 2 report, integrate validated web-sized outputs
into the existing GitHub maps, preserving current layers, access behavior,
attribution and deployment workflow. Source code is published now so it can be
maintained directly. The launchers do not publish site data or invent results.

Rebuild Stage 1 with python build_launcher.py and Stage 2 with
python build_stage2_launcher.py after editing bundled files. Run
python -m unittest discover -s tests -v from this directory.
Tests cover verified inputs, bundle integrity, topology, directed paths,
project identities, FEWS missing values and mass/item separation. Actual
Djibouti PBF parsing and an independent GEOS/PROJ spatial adapter also passed.
The adapter does not validate native QGIS bindings, providers, GUI or project
saving; those need the first local Stage 2 run.

Sources:
[Geofabrik](https://download.geofabrik.de/africa.html),
[OSM licence](https://www.openstreetmap.org/copyright),
[FEWS API](https://help.fews.net/fde/v3/fews-net-api),
[QGIS geometry API](https://api.qgis.org/api/3.38/classQgsGeometry.html).
