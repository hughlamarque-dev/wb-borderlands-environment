'use strict';
(() => {
  const enc=new TextEncoder(),dec=new TextDecoder();
  const un64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
  let boot=null,key=null,manifest=null,epoch=0,routeSerial=0;
  const cache=new Map(),urls=new Set();
  const el=id=>document.getElementById(id);
  const base=new URL('.',location.href);
  async function request(path,format='arrayBuffer'){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000);
    try{
      const r=await fetch(new URL(path,base),{signal:controller.signal,credentials:'same-origin',cache:path.startsWith('assets/')?'force-cache':'no-cache'});
      if(!r.ok)throw new Error('A map file could not be loaded. Please try again.');
      return await r[format]();
    }catch(error){
      if(error.name==='AbortError')throw new Error('The download took too long. Please try again.');
      throw error;
    }finally{clearTimeout(timer);}
  }
  async function decrypt(desc){
    const localKey=key,localEpoch=epoch;if(!localKey)throw new Error('The session is locked.');
    const encrypted=await request(desc.path);
    let clear=await crypto.subtle.decrypt({name:'AES-GCM',iv:un64(desc.nonce),additionalData:enc.encode(boot.build+'|'+desc.id)},localKey,encrypted);
    if(desc.gzip)clear=await new Response(new Blob([clear]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    if(localEpoch!==epoch||!key)throw new Error('The session is locked.');
    return clear;
  }
  async function asset(name){
    if(!key||!manifest)throw new Error('Enter the access password first.');
    if(!manifest[name])throw new Error('The requested map asset is missing: '+name);
    if(!cache.has(name)){
      const p=decrypt(manifest[name]);cache.set(name,p);
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
    const name=(location.hash.slice(1)||'home');
    const allowed=['home','analysis','map=moyale_borana','map=mandera_triangle','map=karamoja','map=dikhil'];
    if(!allowed.includes(name)){location.hash='home';return;}
    // Destroy the previous map before releasing its image URLs and data cache.
    el('view').srcdoc='<!doctype html><p style="font:15px Arial;padding:24px">Opening…</p>';release();
    window.WBVault.status('Opening '+(name.startsWith('map=')?'map':name==='analysis'?'analysis':'maps')+'…');
    try{
      const page=await window.WBVault.text('page/'+name);
      if(serial!==routeSerial||!key)return;
      // srcdoc is same-origin. All executable application content is authenticated
      // before insertion; fetched project text is escaped by the map application.
      el('view').srcdoc=page.replace('<head>','<head><base href="'+base.href.replace(/&/g,'&amp;').replace(/"/g,'&quot;')+'">');
      el('routeLabel').textContent=name.startsWith('map=')?name.slice(4).replace(/_/g,' '):'';
      window.WBVault.status('');
    }catch(e){if(serial===routeSerial){window.WBVault.status('');el('view').srcdoc='<!doctype html><p style="font:15px Arial;padding:24px">This view could not be opened. Use All maps to try again.</p>';}}
  }
  function lock(){epoch++;routeSerial++;key=null;manifest=null;el('view').srcdoc='';release();el('workspace').style.display='none';el('access').style.display='block';el('password').value='';el('message').textContent='';window.WBVault.status('');el('password').focus();}
  el('lock').addEventListener('click',lock);window.addEventListener('hashchange',route);
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
      manifest=JSON.parse(dec.decode(await decrypt(boot.manifest)));el('password').value='';
      el('access').style.display='none';el('workspace').style.display='block';await route();
    }catch(e){key=null;manifest=null;el('message').textContent=e.message||'The site could not be unlocked. Please try again.';}
    finally{el('unlockButton').disabled=false;}
  });
})();
