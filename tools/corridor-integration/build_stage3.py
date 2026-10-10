"""Validate reviewed native Stage 2 exports and package them for the existing vault.

Usage: python build_stage3.py REVIEW_DIRECTORY SITE_CHECKOUT
The checkout must contain boot.json, its public encrypted manifest and baseline
assets. This produces an explicit changes.json; it does not upload anything.
"""
from __future__ import annotations
import collections
import csv
import gzip
import hashlib
import json
import math
import pathlib
import sys
import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CODES = ('moyale_borana', 'mandera_triangle', 'karamoja', 'dikhil')

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def web_document(code, **values):
    return dict(format='wb-corridors-web-v1', code=code, reviewed='2026-10-10', **values)

def features(root, code, kind):
    document=read_json(root/'web_outputs'/f'{code}_{kind}.geojson')
    assert document['type']=='FeatureCollection'
    return document['features']

def round_geometry(geometry):
    def rounded(v):
        return [rounded(x) for x in v] if isinstance(v,list) else round(v,6)
    return dict(type=geometry['type'],coordinates=rounded(geometry['coordinates']))

def prepare(review):
    report=read_json(review/'stage2_status.json')
    assert report['status']=='PROCESSED_READY_FOR_REVIEW'
    assert report['original_project_file_unchanged'] and report['output_project_opened']
    assert len({report[k] for k in ('original_project_sha256_before','original_project_sha256_after_processing','original_project_sha256_after_copy')})==1
    assert report['gems_matches_original_upload'] and not report['gems_audit']['invalid_rows']
    assert not report['gems_audit']['unverified_project_metadata']
    assert report['metric_version']=='major-road-junction-nodes-v1'
    projects={p['project_id']:p for p in read_json(review/'web_outputs/gems_projects.json')}
    with (review/'web_outputs/fews_monthly_observations.csv').open(encoding='utf-8-sig',newline='') as f:
        monthly=list(csv.DictReader(f))
    assert len(monthly)==sum(p['published_monthly_rows'] for p in report['fews_audit']['points'])
    assert len({r['observation_identity'] for r in monthly})==len(monthly)
    series=collections.defaultdict(list)
    for row in monthly:
        assert row['data_usage_policy']=='Public' and row['collection_status']=='Published'
        assert row['duplicate_series_month']=='False'
        assert row['unit_type'] in ('Weight','Item','Volume')
        quantity=float(row['common_unit_quantity']);assert math.isfinite(quantity) and quantity>=0
        if row['unit_type']=='Weight':
            assert row['common_unit']=='kg' and math.isclose(float(row['mass_tonnes']),quantity/1000,rel_tol=1e-12,abs_tol=1e-12)
        else:assert row['mass_tonnes']==''
        series[(row['border_point_id'],row['dataseries'])].append(row)
    series_documents={}
    metadata_fields=['border_point_id','dataseries','reporting_country','reporting_country_code','border_point','source','source_country_code','destination','destination_country_code','cpcv2','product','flow_type','trade_type','unit_type','unit_name','common_unit','source_organization','source_document','collection_schedule','data_usage_policy']
    observation_fields=['period_date','start_date','source_csv_row','observation_identity','raw_published_records_in_month','status','id','value']
    for key,rows in series.items():
        rows.sort(key=lambda r:r['period_date'])
        # Retain identities instead of combining reporters or commodity series.
        for field in metadata_fields:assert len({r[field] for r in rows})==1,(key,field)
        assert len({r['period_date'][:7] for r in rows})==len(rows),key
        doc={field:rows[0][field] for field in metadata_fields}
        doc['key']='|'.join(key)
        doc['display_unit']='tonnes' if doc['unit_type']=='Weight' else {'ea':'items','L':'litres'}[doc['common_unit']]
        doc['observations']=[dict({k:r[k] for k in observation_fields},common_unit_quantity=float(r['common_unit_quantity']),mass_tonnes=float(r['mass_tonnes']) if r['mass_tonnes'] else None) for r in rows]
        series_documents[key]=doc
    outputs={};checks=[]
    for code in CODES:
        summary=next(m for m in report['maps'] if m['code']==code)
        cells=[f['properties'] for f in features(review,code,'connectivity_hexes')]
        nodes=features(review,code,'junction_nodes')
        assert len(cells)==summary['hexes'] and len(nodes)==summary['junction_nodes']
        assert len({p['cell_id'] for p in cells})==len(cells)
        assert len({f['properties']['osm_node_id'] for f in nodes})==len(nodes)
        assert not summary['junction_nodes_unassigned_to_grid'] and summary['length_conservation_error_m']<.001
        assert summary['grid_boundary_difference_area_ratio']<.001
        assert sum(p['junction_nodes'] or 0 for p in cells)==len(nodes)
        assert math.isclose(sum(p['major_road_km'] for p in cells),summary['major_road_km_assigned_to_grid'],abs_tol=1e-7)
        by_cell=collections.defaultdict(list)
        for f in nodes:by_cell[f['properties']['cell_id']].append(f['properties'])
        for p in cells:
            if not p['metric_applicable']:
                assert p['major_road_km']==0 and p['junction_nodes'] is None
            else:
                assert p['junction_nodes']==len(by_cell[p['cell_id']])
                if p['major_road_km']>=.5:assert math.isclose(p['junction_nodes_per_10km'],10*p['junction_nodes']/p['major_road_km'],rel_tol=1e-10,abs_tol=1e-10)
                else:assert p['junction_nodes_per_10km'] is None
            for prefix,path in [('urban','town_path_km'),('place','place_path_km')]:
                for distance in [2,5,10]:
                    field=f'{prefix}_{distance}km'
                    expected=sum(n[path] is not None and n[path]<=distance for n in by_cell[p['cell_id']])
                    assert p[field+'_nodes']==(expected if p['metric_applicable'] else None)
                    share=p[field+'_share']
                    if p['junction_nodes']:assert math.isclose(share,expected/p['junction_nodes'],abs_tol=1e-12)
                    else:assert share is None
        roads=features(review,code,'major_roads')
        road_fc={'type':'FeatureCollection','features':[dict(type='Feature',geometry=round_geometry(f['geometry']),properties={k:f['properties'][k] for k in ['osm_way_id','highway','name','ref','snapshot']}) for f in roads]}
        outputs[code+'/connectivity']=web_document(code,metric_version=report['metric_version'],summary={k:summary[k] for k in ['hexes','junction_nodes','major_road_km']},cells=cells,nodes={'type':'FeatureCollection','features':nodes},roads=road_fc)
        gems=features(review,code,'gems_locations');quarantine=features(review,code,'gems_review')
        assert len(gems)+len(quarantine)==summary['gems_source_rows_in_focus']
        assert len(quarantine)==summary['gems_review_rows']
        if code=='mandera_triangle':assert all(f['properties']['project_id']!='P163980' for f in gems)
        location_fields=['source_id','source_row','project_id','lead_practice','source_country','location_date','implementation_status','coordinate_precision','existing_map_relationship','official_title','closing_date','pre2020_closing_review','major_road_straight_distance_km']
        curated=[]
        for f in gems:
            p=f['properties'];assert p['location_date'] is None and p['implementation_status']=='not supplied' and p['coordinate_precision']=='not supplied'
            assert projects[p['project_id']]['metadata_verified']
            fields={k:p[k] for k in location_fields};fields['existing_directory_links']=json.loads(p['existing_directory_links'])
            curated.append(dict(type='Feature',geometry=f['geometry'],properties=fields))
        pids=sorted({f['properties']['project_id'] for f in curated})
        outputs[code+'/gems']=web_document(code,quarantined_count=len(quarantine),source_sha256=report['gems_audit']['source_sha256'],locations={'type':'FeatureCollection','features':curated},projects=[{k:projects[pid][k] for k in ['project_id','official_title','closing_date','approval_date','official_source_url']} for pid in pids])
        places=[]
        for f in features(review,code,'fews_places'):
            p=f['properties'];point_ids=json.loads(p['point_ids']);assert 142975 not in point_ids
            selected=[doc for (point_id,_),doc in series_documents.items() if int(point_id) in point_ids]
            assert sum(len(s['observations']) for s in selected)==p['published_series_rows']
            places.append(dict(name=p['name'],geometry=f['geometry'],source_coordinates=json.loads(p['source_coordinates']),series=selected))
        outputs[code+'/fews']=web_document(code,places=places,source='FEWS NET Data Warehouse',catalogue_reference_date='2026-10-09',quarantine_note='Balho coordinates require verification; 40 historical rows are retained in the local review export, without a map position.')
        checks.append(dict(code=code,cells=len(cells),junction_nodes=len(nodes),road_cells=sum(bool(p['metric_applicable']) for p in cells),gems_published_rows=len(curated),gems_quarantined_rows=len(quarantine),gems_project_ids=len(pids),fews_physical_places=len(places),fews_monthly_observations=sum(len(s['observations']) for p in places for s in p['series'])))
    audit=dict(status='PASSED',reviewed='2026-10-10',native_qgis_version='3.38.3-Grenoble',original_project_unchanged=True,metric_version=report['metric_version'],maps=checks,source_report_sha256=hashlib.sha256((review/'stage2_status.json').read_bytes()).hexdigest(),gems_source_sha256=report['gems_audit']['source_sha256'],fews_public_monthly_rows=len(monthly),fews_unplaced_balho_rows=40,limits=report['metric_definitions']['limitations'])
    return outputs,audit

def amend_page(html):
    if 'window.createWBCorridors({DATA,map' in html:
        assert 'corridors/corridor-integration.js?v=2026-10-10.2' in html
        return html
    def replace(old,new):
        nonlocal html
        assert html.count(old)==1,old[:100]
        html=html.replace(old,new)
    replace('<script src="vendor/leaflet.markercluster.js"></script>', '<script src="vendor/leaflet.markercluster.js"></script>\n<script src="corridors/corridor-integration.js?v=2026-10-10.2"></script>')
    replace('data-preset="transport">Towns &amp; infrastructure','data-preset="transport">Corridors &amp; infrastructure')
    replace("const transportThemes={crossings:'Towns & transport',infrastructure:'Energy & infrastructure'};", "const transportThemes={connectivity:'Local road connectivity',fews:'Observed border trade',crossings:'Towns & transport',infrastructure:'Energy & infrastructure'};")
    replace("let transportTheme='crossings';", "let transportTheme='connectivity';")
    replace("box.appendChild(buttons);return;", "box.appendChild(buttons);corridors.controls(name,box);return;")
    replace("if(!themeChoices[group])return;", "if(!themeChoices[group]){corridors.controls(name,box);return;}")
    replace('projects:[],dataHex:', "projects:['gems'],connectivity:['hexes','connectivity'],fews:['fews'],dataHex:")
    replace("async function ensureGroup(group){", "async function ensureGroup(group){\n  if(await corridors.load(group))return;")
    replace("requestedPreset=name;viewReady=false;", "requestedPreset=name;viewReady=false;corridors.prepare(name);")
    replace("  refreshLegend(name);\n}\nfunction legendItem", "  corridors.draw(name);\n  refreshLegend(name);\n}\nfunction legendItem")
    replace('  refreshContextLegend(name);', '  refreshContextLegend(name);\n  corridors.legend(name);')
    replace("  pEl('mobileLegend').innerHTML=legend.innerHTML;\n}", "  pEl('mobileLegend').innerHTML=legend.innerHTML;\n  corridors.legend(name);\n}")
    replace("      renderProjectMap();\n    }\n  }catch", "      renderProjectMap();\n    }\n    corridors.ready(name);\n  }catch")
    replace("if(currentContext.has(\"majorCorridors\")){", "if(currentContext.has(\"majorCorridors\")&&currentPreset!==\"connectivity\"){")
    detail_end='}\nfunction downloadProjects(){'
    replace(detail_end,'  corridors.projectDetails(p,detail);\n'+detail_end)
    replace("  if(projects||views)pEl('mobileKey').open=false;", "  if(projects||views)pEl('mobileKey').open=false;\n  if(typeof corridors!=='undefined')corridors.syncTradeNav();")
    initialization="""const corridors=window.createWBCorridors({DATA,map,svgRenderer,setPreset,currentView:()=>currentPreset,openProject:async link=>{
  await ensureProjects();
  if(link.scope==='context'){pEl('projectScope').value='all';await filterProjects();}
  const match=projectFamilies.find(p=>p.project_id===link.family_id||p.records.some(r=>r.project_id===link.directory_id));
  if(match)showProject(match.project_id,false);
}});
"""
    replace("map.on('zoomend',syncRoadDetail);",initialization+"map.on('zoomend',syncRoadDetail);")
    replace('setupProjectNavigation();', 'setupProjectNavigation();\ncorridors.setup();')
    replace('await setPreset("clean");', "const startView=new URLSearchParams(parent.location.search).get('view');\nawait setPreset(['connectivity','fews','projects'].includes(startView)?startView:'clean');")
    source_rows="""<tr><td>Local major-road connectivity</td><td>© OpenStreetMap contributors, Geofabrik country extracts dated 8 October 2026; processed in QGIS 3.38.3.</td><td>Open Database Licence, ODbL 1.0; derived grid and node data follow the contributing source.</td><td>Raw shared major/local OSM junction nodes, not merged functional interchanges. Major roads are motorway/trunk/primary and links, not a named corridor centreline. Directed static motor-access paths exclude major roads and include a place snap ≤250 m. City/town nodes and city/town/village nodes are separate; thresholds are 2, 5 and 10 km with a 15 km halo. Road length is mapped node-pair length; divided roads can add carriageway length and nodes. Normalisation requires ≥0.5 km per cell; shares require junctions. Conditional access is excluded. Turn restrictions, live closures and passability are not modelled. No traffic, population-access or economic-impact claim. <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OSM attribution and licence</a>.</td></tr>
<tr><td>Historical border trade</td><td>FEWS NET Data Warehouse, published monthly cross-border trade observations (formal and informal series); catalogue checked 9 October 2026.</td><td>Only rows labelled Public and Published are included. Retain FEWS NET attribution, reporting-side identity, original units and source document.</td><td>One marker per physical place; commodities, reporters, directions and series are separate. Weight in kg is converted to tonnes, item and volume quantities remain separate. Gaps are missing observations, not zero. Raw record count is not days monitored or a completeness percentage. Nimule, Moyale, Belet Hawo/Bula Hawo, Galafi and Loyado have historical observations; Balho is unplaced pending coordinate verification. No inferred highway routes or total traffic. <a href="https://fdw.fews.net/api/borderpoint/" target="_blank" rel="noopener noreferrer">FEWS catalogue</a>.</td></tr>
<tr><td>GEMS source project locations</td><td>User-supplied World Bank GEMS CSV; official titles and project IDs matched against the World Bank project API.</td><td>Source attribution: World Bank GEMS; official project metadata: World Bank Projects and Operations. The supplied CSV contained no location-level licence or observation date.</td><td>GEMS coordinates remain separate from the project directory’s published evidence. Links use exact project IDs, including explicitly identified associated operations; family links do not merge different World Bank IDs. Implementation status, coordinate precision and observation date were not supplied; country labels were not independently validated. Source rows are not necessarily distinct sites. Mandera marine-fisheries point P163980 is withheld pending verification. P096367 retains a historical closing-date flag. Straight-line distance to a major road is not road access. <a href="https://projects.worldbank.org/" target="_blank" rel="noopener noreferrer">Official project catalogue</a>.</td></tr>
"""
    replace('        </tbody>\n      </table></div>',source_rows+'        </tbody>\n      </table></div>')
    return html

def build(review,site):
    outputs,audit=prepare(review)
    boot=read_json(site/'boot.json');assert boot['access']=='public'
    aes=AESGCM(base64.b64decode(boot['public_key']))
    d=boot['manifest'];clear=aes.decrypt(base64.b64decode(d['nonce']),(site/d['path']).read_bytes(),(boot['build']+'|'+d['id']).encode());manifest=json.loads(gzip.decompress(clear))
    changed=[]
    def asset(raw,mime):
        identifier=hashlib.sha256(raw).hexdigest()[:32];path='assets/'+identifier+'.bin';nonce=os.urandom(12)
        ciphertext=aes.encrypt(nonce,gzip.compress(raw,mtime=0),(boot['build']+'|'+identifier).encode())
        (site/path).write_bytes(ciphertext);changed.append(path)
        return dict(path=path,id=identifier,nonce=base64.b64encode(nonce).decode(),gzip=True,mime=mime,bytes=len(ciphertext))
    for name,document in outputs.items():manifest[name]=asset(json.dumps(document,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode(),'application/json')
    (site/'decoded').mkdir(parents=True,exist_ok=True)
    for code in CODES:
        name='page/map='+code;descriptor=manifest[name]
        raw=aes.decrypt(base64.b64decode(descriptor['nonce']),(site/descriptor['path']).read_bytes(),(boot['build']+'|'+descriptor['id']).encode())
        html=amend_page(gzip.decompress(raw).decode())
        (site/'decoded'/('new_'+code+'.html')).write_text(html)
        manifest[name]=asset(html.encode(),'text/html')
    boot['manifest']=asset(json.dumps(manifest,separators=(',',':')).encode(),'application/json')
    boot['ui_revision']='2026-10-10.corridors.30'
    (site/'boot.json').write_text(json.dumps(boot,separators=(',',':'))+'\n');changed.append('boot.json')
    audit_path='tools/corridor-integration/validation/stage3_review_2026-10-10.json';f=site/audit_path;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(json.dumps(audit,indent=2)+'\n');changed.append(audit_path)
    changed.append('corridors/corridor-integration.js')
    (site/'changes.json').write_text(json.dumps(changed,indent=2));print(json.dumps(audit,indent=2));print('Changed files:',len(changed))

if __name__=='__main__':build(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))
