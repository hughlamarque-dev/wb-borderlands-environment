'use strict';
(() => {
  const enc=new TextEncoder(),dec=new TextDecoder();
  const un64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
  let boot=null,key=null,manifest=null,epoch=0,routeSerial=0,previewKey=null,previewEpoch=0;
  const cache=new Map(),urls=new Set();
  const el=id=>document.getElementById(id);
  const base=new URL('.',location.href);
  async function request(path,format='arrayBuffer'){
    for(let attempt=0;attempt<2;attempt++){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
      try{
        const r=await fetch(new URL(path,base),{signal:controller.signal,credentials:'same-origin',cache:attempt?'reload':'no-cache'});
        if(!r.ok){const error=new Error('A map file could not be loaded. Please try again.');error.code='HTTP_ERROR';error.status=r.status;error.assetPath=path;throw error;}
        return await r[format]();
      }catch(error){
        if(attempt===0)continue;
        if(error.name==='AbortError'){const timeout=new Error('The download took too long. Please try again.');timeout.code='DOWNLOAD_TIMEOUT';timeout.assetPath=path;throw timeout;}
        error.assetPath=path;if(!error.code)error.code='DOWNLOAD_FAILED';
        throw error;
      }finally{clearTimeout(timer);}
    }
  }
  async function assetBytes(desc){
    const safePath=p=>typeof p==='string'&&p===p.trim()&&/^assets\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(p);
    if(!safePath(desc.path)||!Number.isSafeInteger(desc.bytes)||desc.bytes<0)throw new Error('Invalid map asset descriptor.');
    const mapping=boot.asset_parts;
    if(mapping!==undefined&&(!mapping||typeof mapping!=='object'||Array.isArray(mapping)))throw new Error('Invalid map asset parts.');
    const split=mapping&&Object.prototype.hasOwnProperty.call(mapping,desc.path);
    const paths=split?mapping[desc.path]:[desc.path];
    if(!Array.isArray(paths)||!paths.length||paths.length>1024||!paths.every(safePath))throw new Error('Invalid map asset parts.');
    const chunks=[];let bytes=0;
    for(let offset=0;offset<paths.length;offset+=4){
      const batch=await Promise.all(paths.slice(offset,offset+4).map(path=>request(path)));
      for(const chunk of batch){bytes+=chunk.byteLength;if(bytes>desc.bytes)throw new Error('A map file is incomplete. Please try again.');chunks.push(new Uint8Array(chunk));}
    }
    if(bytes!==desc.bytes)throw new Error('A map file is incomplete. Please try again.');
    const joined=new Uint8Array(bytes);let offset=0;
    for(const chunk of chunks){joined.set(chunk,offset);offset+=chunk.byteLength;}
    return joined;
  }
  function patchKey(key){return typeof key==='string'&&key.length>0&&!['__proto__','prototype','constructor'].includes(key);}
  function patchObject(value){return value!==null&&typeof value==='object'&&!Array.isArray(value);}
  function checkPatchValue(value){
    if(Array.isArray(value)){value.forEach(checkPatchValue);return;}
    if(patchObject(value)){for(const key of Object.keys(value)){if(!patchKey(key))throw new Error('Invalid update property.');checkPatchValue(value[key]);}}
  }
  function applyPublicPatch(data,patch,name){
    const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
    if(!patchObject(patch)||patch.format!=='public-json-patch-v1'||patch.asset!==name||!Array.isArray(patch.operations))throw new Error('Invalid public map update.');
    checkPatchValue(patch);
    for(const op of patch.operations){
      if(!patchObject(op)||!Array.isArray(op.path)||!op.path.every(patchKey))throw new Error('Invalid update path.');
      const at=path=>{let value=data;for(const key of path){if(!patchObject(value)||!own(value,key))throw new Error('Missing update path.');value=value[key];}return value;};
      if(op.op==='set'){
        if(!own(op,'value'))throw new Error('Missing update value.');
        if(!op.path.length)data=op.value;
        else{const parent=at(op.path.slice(0,-1));if(!patchObject(parent))throw new Error('Invalid update parent.');parent[op.path.at(-1)]=op.value;}
      }else if(op.op==='delete'){
        if(!op.path.length)throw new Error('Cannot delete the update root.');
        const parent=at(op.path.slice(0,-1)),key=op.path.at(-1);if(!patchObject(parent)||!own(parent,key))throw new Error('Missing update property.');delete parent[key];
      }else{
        const rows=at(op.path),key=op.key;
        if(!Array.isArray(rows)||!patchKey(key))throw new Error('Invalid update row target.');
        const validId=id=>typeof id==='string'||(typeof id==='number'&&Number.isFinite(id));
        const byId=new Map();for(const row of rows){if(!patchObject(row)||!own(row,key)||!validId(row[key])||byId.has(row[key]))throw new Error('Missing or duplicate update row identity.');byId.set(row[key],row);}
        const ids=list=>{if(!Array.isArray(list)||list.some(id=>!validId(id))||new Set(list).size!==list.length)throw new Error('Invalid update row identities.');for(const id of list)if(!byId.has(id))throw new Error('Missing update row.');return new Set(list);};
        if(op.op==='remove_rows'){const removed=ids(op.ids);for(let i=rows.length-1;i>=0;i--)if(removed.has(rows[i][key]))rows.splice(i,1);}
        else if(op.op==='update_row'){
          if(!validId(op.id)||!byId.has(op.id))throw new Error('Missing update row.');
          const row=byId.get(op.id),fields=own(op,'set')?op.set:{},deleted=own(op,'delete')?op.delete:[];
          if(!patchObject(fields)||!Array.isArray(deleted)||!deleted.every(patchKey)||new Set(deleted).size!==deleted.length)throw new Error('Invalid update row fields.');
          if(deleted.includes(key)||(own(fields,key)&&fields[key]!==op.id))throw new Error('Cannot change update row identity.');
          for(const field of deleted){if(!own(row,field))throw new Error('Missing update row field.');delete row[field];}
          for(const field of Object.keys(fields)){if(!patchKey(field))throw new Error('Invalid update row field.');row[field]=fields[field];}
        }else if(op.op==='insert_row'){
          if(!Number.isInteger(op.index)||op.index<0||op.index>rows.length||!patchObject(op.value)||!own(op.value,key)||!validId(op.value[key])||byId.has(op.value[key]))throw new Error('Invalid inserted update row.');
          rows.splice(op.index,0,op.value);
        }else if(op.op==='order_rows'){
          ids(op.ids);if(op.ids.length!==rows.length)throw new Error('Update ordering must include every row.');rows.splice(0,rows.length,...op.ids.map(id=>byId.get(id)));
        }else throw new Error('Unknown public update operation.');
      }
    }
    return data;
  }
  async function publicUpdateBytes(file){
    if(!patchObject(file)||typeof file.path!=='string'||file.path!==file.path.trim()||!/^assets\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(file.path)||typeof file.sha256!=='string'||!/^[a-f0-9]{64}$/.test(file.sha256))throw new Error('Invalid public update file.');
    const bytes=await request(file.path),digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');
    if(digest!==file.sha256){const error=new Error('A public map update could not be verified.');error.code='UPDATE_INTEGRITY_MISMATCH';error.assetPath=file.path;error.receivedBytes=bytes.byteLength;error.actualDigest=digest;throw error;}
    return bytes;
  }
  async function decryptBase(desc,localKey){
    const encrypted=await assetBytes(desc);
    let clear=await crypto.subtle.decrypt({name:'AES-GCM',iv:un64(desc.nonce),additionalData:enc.encode(boot.build+'|'+desc.id)},localKey,encrypted);
    if(desc.gzip)clear=await new Response(new Blob([clear]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    return clear;
  }
  function previewConfig(){
    const value=boot.preview_access;if(value===undefined)return null;
    const routes=['map=moyale_borana','map=mandera_triangle','map=dikhil'];
    if(!patchObject(value)||value.format!=='preview-access-v1'||!Array.isArray(value.routes)||value.routes.length!==3||new Set(value.routes).size!==3||!value.routes.every(v=>routes.includes(v)))throw new Error('Invalid development access configuration.');
    return value;
  }
  function previewRoute(name){return previewConfig()?.routes.includes(name)||false;}
  async function unwrapPreview(password){
    const config=previewConfig();
    if(!config||config.kdf?.name!=='PBKDF2'||config.kdf?.hash!=='SHA-256'||config.kdf?.iterations!==600000||typeof config.kdf?.salt!=='string'||un64(config.kdf.salt).length!==16||typeof config.wrap?.nonce!=='string'||un64(config.wrap.nonce).length!==12||typeof config.wrap?.ciphertext!=='string'||un64(config.wrap.ciphertext).length!==48)throw new Error('Invalid development access configuration.');
    const material=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveKey']);
    const wrapping=await crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt:un64(config.kdf.salt),iterations:config.kdf.iterations},material,{name:'AES-GCM',length:256},false,['decrypt']);
    let raw;
    try{raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:un64(config.wrap.nonce),additionalData:enc.encode('preview:'+boot.build)},wrapping,un64(config.wrap.ciphertext));}
    catch(_){throw new Error('The password was not accepted. Please try again.');}
    try{return await crypto.subtle.importKey('raw',raw,'AES-GCM',false,['decrypt']);}
    finally{new Uint8Array(raw).fill(0);}
  }
  async function protectedPage(file,name,localPreviewKey){
    if(!localPreviewKey||!previewRoute(name.slice(5)))throw new Error('Enter the development access password first.');
    const page=JSON.parse(dec.decode(await publicUpdateBytes(file)));
    if(!patchObject(page)||Object.keys(page).length!==5||page.format!=='password-page-v1'||page.asset!==name||page.gzip!==true||typeof page.nonce!=='string'||un64(page.nonce).length!==12||typeof page.ciphertext!=='string')throw new Error('Invalid protected page envelope.');
    let clear=await crypto.subtle.decrypt({name:'AES-GCM',iv:un64(page.nonce),additionalData:enc.encode('preview:'+boot.build+'|'+name)},localPreviewKey,un64(page.ciphertext));
    return new Response(new Blob([clear]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  }
  async function decrypt(desc,name){
    const localKey=key,localEpoch=epoch;if(!localKey)throw new Error('The session is locked.');
    const localPreviewKey=boot.preview_access?previewKey:null,localPreviewEpoch=boot.preview_access?previewEpoch:0;
    const updates=boot.public_updates;
    if(updates!==undefined&&(boot.access!=='public'||!patchObject(updates)))throw new Error('Invalid public map updates.');
    const hasUpdate=updates&&Object.prototype.hasOwnProperty.call(updates,desc.path),update=hasUpdate?updates[desc.path]:null;
    let clear;
    if(hasUpdate){
      if(!patchObject(update)||Object.keys(update).length!==1)throw new Error('Invalid public map update.');
      if(Object.prototype.hasOwnProperty.call(update,'file')){
        if(desc.mime!=='text/html'||typeof name!=='string'||!name.startsWith('page/'))throw new Error('Invalid public page update.');
        const file=update.file;
        if(!patchObject(file)||(Object.prototype.hasOwnProperty.call(file,'format')&&!['public-page-v1','password-page-v1'].includes(file.format)))throw new Error('Invalid public page format.');
        clear=file.format==='password-page-v1'?await protectedPage(file,name,localPreviewKey):await publicUpdateBytes(file);
        if(file.format==='public-page-v1'){
          const page=JSON.parse(dec.decode(clear));
          if(!patchObject(page)||Object.keys(page).length!==3||page.format!=='public-page-v1'||page.asset!==name||typeof page.html!=='string')throw new Error('Invalid public page envelope.');
          clear=enc.encode(page.html);
        }
      }else if(Object.prototype.hasOwnProperty.call(update,'patches')){
        if(desc.mime!=='application/json'||!Array.isArray(update.patches)||!update.patches.length||update.patches.length>1024||typeof name!=='string')throw new Error('Invalid public JSON update.');
        let data=JSON.parse(dec.decode(await decryptBase(desc,localKey)));
        for(let offset=0;offset<update.patches.length;offset+=4){
          const parts=await Promise.all(update.patches.slice(offset,offset+4).map(publicUpdateBytes));
          for(const bytes of parts)data=applyPublicPatch(data,JSON.parse(dec.decode(bytes)),name);
        }
        clear=enc.encode(JSON.stringify(data));
      }else throw new Error('Invalid public map update.');
    }else clear=await decryptBase(desc,localKey);
    if(localEpoch!==epoch||!key)throw new Error('The session is locked.');
    if(boot.preview_access&&name?.startsWith('page/')&&previewRoute(name.slice(5))&&(localPreviewEpoch!==previewEpoch||!previewKey))throw new Error('The development session is locked.');
    return clear;
  }
  async function asset(name){
    if(!key||!manifest)throw new Error('Enter the access password first.');
    if(boot.preview_access&&name.startsWith('page/')&&previewRoute(name.slice(5))&&!previewKey)throw new Error('Enter the development access password first.');
    if(!manifest[name])throw new Error('The requested map asset is missing: '+name);
    if(!cache.has(name)){
      const p=decrypt(manifest[name],name);cache.set(name,p);
      p.catch(()=>{if(cache.get(name)===p)cache.delete(name);});
    }
    return cache.get(name);
  }
  function release(){for(const u of urls)URL.revokeObjectURL(u);urls.clear();cache.clear();}
  window.WBVault={
    json:async name=>JSON.parse(dec.decode(await asset(name))),
    text:async name=>dec.decode(await asset(name)),
    blob:async name=>{const data=await asset(name);const u=URL.createObjectURL(new Blob([data],{type:manifest[name].mime}));urls.add(u);return u;},
    go:target=>{if(location.hash.slice(1)===target)return route();location.hash=target;},
    status:message=>{el('status').textContent=message||'';el('status').style.display=message?'block':'none';}
  };
  async function route(){
    if(!key)return;const serial=++routeSerial;
    const name=(location.hash.slice(1)||boot.default_route||'home');
    const allowed=boot.routes||['home','analysis','map=moyale_borana','map=mandera_triangle','map=karamoja','map=dikhil'];
    if(!allowed.includes(name)){location.hash=boot.default_route||'home';return;}
    const hideToolbar=!!boot.standalone||name==='home';
    el('toolbar').hidden=hideToolbar;el('workspace').classList.toggle('standalone',hideToolbar);
    el('previewAccess').hidden=true;el('view').hidden=false;el('previewPassword').value='';el('previewMessage').textContent='';
    el('lock').hidden=boot.access==='public'&&!previewKey;el('lock').textContent=boot.access==='public'?'Lock previews':'Lock';
    // Destroy the previous map before releasing its image URLs and data cache.
    el('view').srcdoc='<!doctype html><p style="font:15px Arial;padding:24px">Opening…</p>';release();
    if(boot.preview_access&&previewRoute(name)&&!previewKey){
      el('view').srcdoc='';el('view').hidden=true;el('previewAccess').hidden=false;
      const labels={'map=moyale_borana':'Moyale–Borana','map=mandera_triangle':'Mandera Triangle','map=dikhil':'Dikhil'};
      el('previewTitle').textContent=labels[name]+' Cluster';el('previewNotice').textContent=previewConfig().message||'Under Development, see Karamoja Cluster for live example';
      el('routeLabel').textContent=labels[name];window.WBVault.status('');el('previewPassword').focus();return;
    }
    window.WBVault.status('Opening '+(name.startsWith('map=')?'map':name==='analysis'?'analysis':'maps')+'…');
    try{
      const page=await window.WBVault.text('page/'+name);
      if(serial!==routeSerial||!key)return;
      // srcdoc is same-origin. All executable application content is authenticated
      // before insertion; fetched project text is escaped by the map application.
      const mobileStyle="<style id=\"mobile-views-layout-24\">/* Mobile: one continuous panel, never a clipped, independently scrolling view list. */\n@media(max-width:1024px){\n .panel.mobile-open{display:flex;flex-direction:column;gap:12px;overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;background:#fff;scroll-padding-top:64px;-webkit-overflow-scrolling:touch}\n .panel>.panel-heading{position:sticky;top:0;z-index:6;flex:0 0 auto;min-height:44px;padding-bottom:8px;background:#fff;box-shadow:0 -16px 0 0 #fff}\n .panel>.preset-grid{flex:0 0 auto;grid-template-columns:repeat(2,minmax(0,1fr));grid-auto-rows:minmax(50px,auto);overflow:visible;max-height:none;scrollbar-gutter:auto;padding-right:0;gap:6px}\n .panel>.preset-grid button{height:auto;min-height:50px}\n .panel>.view-options,.panel>.map-key,.panel>.panel-footer{flex:0 0 auto;overflow:visible;max-height:none;scrollbar-gutter:auto;padding-right:0}\n}\n</style>";
      const mobileNavigation="<script>document.getElementById('mobileLayers').addEventListener('click',()=>{const panel=document.querySelector('.panel');if(panel.classList.contains('mobile-open'))panel.scrollTop=0;});</script>";
      const displayPage=name.startsWith('map=')&&page.includes('id="mobileLayers"')?page.replace('</head>',mobileStyle+'</head>').replace('</body>',mobileNavigation+'</body>'):page;
      el('view').srcdoc=displayPage.replace('<head>','<head><base href="'+base.href.replace(/&/g,'&amp;').replace(/"/g,'&quot;')+'">');
      el('routeLabel').textContent=name.startsWith('map=')?name.slice(4).replace(/_/g,' '):'';
      window.WBVault.status('');
    }catch(e){if(serial===routeSerial){
      console.error('The requested view could not be opened.',{route:name,error:e});window.WBVault.status('');
      const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
      const text=value=>String(value??'').replace(/https?:\/\/[^\s<>"']+/gi,'[URL omitted]').slice(0,400);
      const details=[['Code',e.code||e.name||'VIEW_LOAD_FAILED'],['Revision',boot.ui_revision||'Unknown'],['Route',name]];
      if(typeof e.assetPath==='string'&&/^(?:boot\.json|assets\/[A-Za-z0-9][A-Za-z0-9._-]*)$/.test(e.assetPath)&&e.assetPath===e.assetPath.trim())details.push(['File',e.assetPath]);
      if(Number.isInteger(e.status))details.push(['HTTP status',e.status]);
      if(Number.isSafeInteger(e.receivedBytes))details.push(['Received bytes',e.receivedBytes]);
      if(typeof e.actualDigest==='string'&&/^[a-f0-9]{64}$/.test(e.actualDigest))details.push(['Received SHA-256',e.actualDigest]);
      el('view').srcdoc='<!doctype html><meta charset="utf-8"><title>Unable to open this view</title><main style="font:15px/1.5 Arial,sans-serif;padding:24px;max-width:760px;color:#183b3b"><h1 style="font-size:22px;margin:0 0 12px">Unable to open this view</h1><p>'+escape(text(e.message||'The requested view could not be loaded.'))+'</p><dl style="font-size:13px;overflow-wrap:anywhere">'+details.map(([label,value])=>'<div style="margin:8px 0"><dt style="font-weight:600">'+escape(label)+'</dt><dd style="margin:0">'+escape(text(value))+'</dd></div>').join('')+'</dl><button style="font:inherit;padding:8px 16px;cursor:pointer" onclick="parent.location.reload()">Try again</button></main>';
    }}
  }
  function lock(){
    if(boot.access==='public'){previewEpoch++;previewKey=null;el('previewPassword').value='';release();return route();}
    epoch++;routeSerial++;key=null;manifest=null;el('view').srcdoc='';release();el('workspace').style.display='none';el('access').style.display='block';el('password').value='';el('message').textContent='';window.WBVault.status('');el('password').focus();
  }
  async function openWorkspace(){
    manifest=JSON.parse(dec.decode(await decrypt(boot.manifest,'__manifest__')));el('password').value='';
    el('access').style.display='none';el('workspace').style.display='block';
    el('lock').hidden=boot.access==='public';
    el('toolbar').hidden=!!boot.standalone;el('workspace').classList.toggle('standalone',!!boot.standalone);
    await route();
  }
  el('lock').addEventListener('click',lock);window.addEventListener('hashchange',route);
  el('previewUnlock').addEventListener('submit',async event=>{
    event.preventDefault();const serial=routeSerial,accessEpoch=previewEpoch;el('previewMessage').textContent='';el('previewUnlockButton').disabled=true;
    const password=el('previewPassword').value;el('previewPassword').value='';
    try{
      const unlocked=await unwrapPreview(password);
      if(serial!==routeSerial||accessEpoch!==previewEpoch)return;
      previewKey=unlocked;await route();
    }catch(error){if(serial===routeSerial){el('previewMessage').textContent=error.message||'This preview could not be unlocked.';el('previewPassword').focus();}}
    finally{el('previewUnlockButton').disabled=false;}
  });
  el('unlock').addEventListener('submit',async event=>{
    event.preventDefault();el('message').textContent='';el('unlockButton').disabled=true;
    try{
      if(!window.isSecureContext||!crypto.subtle||!window.DecompressionStream)throw new Error('Use a current browser on the HTTPS website, or the included local preview server.');
      boot=await request('boot.json','json');
      const material=await crypto.subtle.importKey('raw',enc.encode(el('password').value),'PBKDF2',false,['deriveKey']);
      const wrapping=await crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt:un64(boot.kdf.salt),iterations:boot.kdf.iterations},material,{name:'AES-GCM',length:256},false,['decrypt']);
      let raw;
      try{raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:un64(boot.wrap.nonce),additionalData:enc.encode('wrap:'+boot.build)},wrapping,un64(boot.wrap.ciphertext));}
      catch(_){throw new Error('The password was not accepted. Please try again.');}
      key=await crypto.subtle.importKey('raw',raw,'AES-GCM',false,['decrypt']);new Uint8Array(raw).fill(0);
      await openWorkspace();
    }catch(e){key=null;manifest=null;el('message').textContent=e.message||'The site could not be unlocked. Please try again.';}
    finally{el('unlockButton').disabled=false;}
  });
  async function start(){
    try{
      boot=await request('boot.json','json');
      if(boot.access==='public'){key=await crypto.subtle.importKey('raw',un64(boot.public_key),'AES-GCM',false,['decrypt']);await openWorkspace();}
      else{el('access').style.display='block';el('password').focus();}
    }catch(e){window.WBVault.status('The maps could not start. Refresh this page to retry.');console.error(e);}
  }
  start();
})();
