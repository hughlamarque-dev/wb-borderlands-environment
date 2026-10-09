# World Bank corridor integration

Stage 1 is implemented here. Connectivity processing and live-map integration
are **not** implemented by this launcher. Keeping the code in the existing
repository allows subsequent changes to be made directly on GitHub.

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
no administrator privileges. It has not yet been run on Hugh's Windows/QGIS
installation.

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

## What the launcher does and does not do

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

## Stage 2: processing specification, pending implementation

Use the current map boundaries and approximately 10 km hexagons. Review a
named corridor spine rather than treating every primary street as a corridor.
Build network topology from shared OSM node IDs, merging overlapping country
extracts by OSM identity/version. Include local streets and preserve access,
one-way, bridge, tunnel and layer tags. Grade-separated crossings without a
connecting ramp are not junctions. Merge the nodes belonging to one functional
interchange; do not count degree-two segmentation nodes as access junctions.

Proposed outputs are unique local-access junctions per hexagon, junctions per
10 km of corridor, and the count/share with an **off-corridor** local-network path
to a mapped town/village within 5 km. Test 2/5/10 km sensitivity, preserve the
town-to-road snap distance, and use a network halo before clipping. Keep place
types so villages are not labelled urban. Use a metric CRS, assign each junction
to one hexagon, flag small corridor-length denominators, and leave no-corridor
cells not applicable rather than zero. A lack of mapped feeders may reflect
OSM incompleteness; it is not proof of territorial isolation or economic impact.

The GHSL 2030 layers in the older inventory are not a present-day settlement
baseline. Initial results concern access to **mapped settlements**, not access
for all residents. Historical settlement footprints and a population-access
metric need separate source/units/coverage checks and verified downloads if
missing. Existing population rasters are not automatically treated as compatible.

GEMS integration uses exact World Bank project IDs, one canonical project with
many source location records, and separate family/financing relationships.
Retain source row identities and precision/coverage limitations; do not equate a
coordinate with a completed intervention. Reconcile with the existing directory,
promote context-only projects only after scope review, quarantine questionable
locations, and never allocate a whole project's financing to each point.
Keep the user's original GEMS CSV locally until publication eligibility and
the required public fields have been reviewed.

Stage 2 should save new GeoPackages and a separate QGIS project copy. Stage 3
should add only validated web-sized outputs to the existing GitHub maps,
preserving current layers, password/access behavior, source attribution and
the deployment workflow. No invented metric values, inferred traffic flows,
placeholder density scores or unverified GEMS coordinates should be published.

## Sources and maintenance

- [Geofabrik Africa downloads](https://download.geofabrik.de/africa.html)
- [OpenStreetMap copyright and ODbL](https://www.openstreetmap.org/copyright)
- [FEWS NET API documentation](https://help.fews.net/fde/v3/fews-net-api)
- [QGIS command-line startup options](https://docs.qgis.org/3.44/en/docs/user_manual/introduction/qgis_configuration.html)

Rebuild the self-contained launcher after editing its three bundled source files
or the PowerShell template: `python build_launcher.py`. Run
`python -m unittest discover -s tests -v` from this directory. Tests cover the
download validation and bundle integrity; Windows/QGIS GUI behavior still
requires a real local run. All scripts live in this repository, while raw
downloaded inputs and local reports remain on the user's computer.
