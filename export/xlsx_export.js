/* Browser export: populate the artifact-tool designed template; ZIP packaging
   uses stored entries, with no spreadsheet dependency or external data service. */
(function(root){
 'use strict';
 const encoder=new TextEncoder();
 const xml=v=>String(v??'').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g,'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
 const column=i=>{let s='';for(i++;i;i=Math.floor((i-1)/26))s=String.fromCharCode(65+(i-1)%26)+s;return s;};
 function dateValue(s){
  if(typeof s!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(s))return s;
  const d=new Date(s+'T00:00:00Z');
  return Number.isFinite(+d)&&d.toISOString().slice(0,10)===s&&s>='1900-03-01'?Math.round(+d/86400000)+25569:s;
 }
 function cell(ref,value,style){
  const attr=' r="'+ref+'" s="'+style+'"';
  if(value==null||value==='')return '<x:c'+attr+'/>';
  if(typeof value==='object'&&value.formula)return '<x:c'+attr+'><x:f>'+xml(value.formula)+'</x:f><x:v>'+value.cached+'</x:v></x:c>';
  if(typeof value==='number'&&Number.isFinite(value))return '<x:c'+attr+'><x:v>'+value+'</x:v></x:c>';
  // Explicit inline strings: titles, identifiers and source text can never execute as formulas.
  return '<x:c'+attr+' t="inlineStr"><x:is><x:t xml:space="preserve">'+xml(String(value).slice(0,32767))+'</x:t></x:is></x:c>';
 }
 function row(n,values,styles,height){return '<x:row r="'+n+'" ht="'+height+'" customHeight="1">'+values.map((v,i)=>cell(column(i)+n,v,styles[i]||0)).join('')+'</x:row>';}
 function prepareSheet(spec,body,overrides){
  const fixed=spec.rows.filter(r=>spec.name==='Overview'||r.n<=5);
  let data=fixed.map(r=>row(r.n,r.values.map((v,i)=>Object.hasOwn(overrides,column(i)+r.n)?overrides[column(i)+r.n]:v),r.styles,r.height)).join('');
  if(body){
   for(let i=0;i<Math.max(1,body.length);i++){
    const source=spec.rows.find(r=>r.n===6+i%2),values=body[i]||Array(source.styles.length).fill('');
    const lines=Math.max(...values.map((v,j)=>String(v??'').split('\n').reduce((n,s)=>n+Math.max(1,Math.ceil(s.length/Math.max(10,spec.widths[j]-2))),0)));
    data+=row(i+6,values,source.styles,Math.min(300,Math.max(55,lines*14+12)));
   }
  }
  let result=spec.xml.replace(/<x:sheetData>[\s\S]*?<\/x:sheetData>/,'<x:sheetData>'+data+'</x:sheetData>');
  const pane=spec.name==='Overview'?'<x:pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/>':'<x:pane xSplit="1" ySplit="5" topLeftCell="B6" activePane="bottomRight" state="frozen"/>';
  result=result.replace(/<x:sheetViews>[\s\S]*?<\/x:sheetViews>/,'<x:sheetViews><x:sheetView showGridLines="0" workbookViewId="0">'+pane+'</x:sheetView></x:sheetViews>');
  return result;
 }
 let crcTable;
 function crc32(bytes){
  if(!crcTable)crcTable=Uint32Array.from({length:256},(_,n)=>{for(let i=0;i<8;i++)n=n&1?0xEDB88320^(n>>>1):n>>>1;return n>>>0;});
  let crc=0xFFFFFFFF;for(const b of bytes)crc=crcTable[(crc^b)&255]^(crc>>>8);return (crc^0xFFFFFFFF)>>>0;
 }
 function zip(files){
  const chunks=[],central=[];let offset=0,centralSize=0;
  for(const [name,content] of Object.entries(files)){
   const filename=encoder.encode(name),bytes=encoder.encode(content),crc=crc32(bytes);
   const header=new Uint8Array(30+filename.length),h=new DataView(header.buffer);
   h.setUint32(0,0x04034b50,true);h.setUint16(4,20,true);h.setUint16(6,0x800,true);h.setUint16(12,33,true);h.setUint32(14,crc,true);h.setUint32(18,bytes.length,true);h.setUint32(22,bytes.length,true);h.setUint16(26,filename.length,true);header.set(filename,30);
   const entry=new Uint8Array(46+filename.length),e=new DataView(entry.buffer);
   e.setUint32(0,0x02014b50,true);e.setUint16(4,20,true);e.setUint16(6,20,true);e.setUint16(8,0x800,true);e.setUint16(14,33,true);e.setUint32(16,crc,true);e.setUint32(20,bytes.length,true);e.setUint32(24,bytes.length,true);e.setUint16(28,filename.length,true);e.setUint32(42,offset,true);entry.set(filename,46);
   chunks.push(header,bytes);central.push(entry);offset+=header.length+bytes.length;centralSize+=entry.length;
  }
  const end=new Uint8Array(22),e=new DataView(end.buffer);e.setUint32(0,0x06054b50,true);e.setUint16(8,central.length,true);e.setUint16(10,central.length,true);e.setUint32(12,centralSize,true);e.setUint32(16,offset,true);
  return new Blob([...chunks,...central,end],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
 }
 function workbook(template,snapshot){
  const files={...template.files},pRows=snapshot.projects.map(r=>r.map((v,i)=>i===4||i===5?dateValue(v):v)),lRows=snapshot.locations;
  const count=(sheet,col,rows)=>rows?{formula:'COUNTA(\''+sheet+'\'!'+col+'6:'+col+(rows+5)+')',cached:rows}:0;
  const overview={A3:snapshot.cluster+' · filtered project export',A6:count('Projects','I',pRows.length),C6:count('Locations','G',lRows.length),E6:snapshot.activityCount,B10:snapshot.cluster,B11:snapshot.exported.replace('T',' ').replace(/\.\d+Z$/,' UTC'),B12:snapshot.filters.search||'All projects',B13:snapshot.filters.outcome,B14:snapshot.filters.organisation,B15:snapshot.filters.period,B16:snapshot.filters.geography};
  for(const spec of template.sheets){const rows=spec.name==='Projects'?pRows:spec.name==='Locations'?lRows:null;files[spec.path]=prepareSheet(spec,rows,spec.name==='Overview'?overview:{A3:snapshot.cluster+' · '+rows.length.toLocaleString()+(spec.name==='Projects'?' matching project families':' matching location references')});}
  for(const [name,last,n] of [['xl/tables/table1.xml','N',pRows.length],['xl/tables/table2.xml','I',lRows.length]]){
   const ref='A5:'+last+Math.max(6,n+5);files[name]=files[name].replace(/ref="A5:[^"]+"/,'ref="'+ref+'"').replace('<x:tableColumns','<x:autoFilter ref="'+ref+'"/><x:tableColumns');
  }
  // No external services or links execute; complete source URLs are exported as text.
  return zip(files);
 }
 root.WBProjectExcel={workbook,dateValue};
})(typeof window==='undefined'?globalThis:window);
