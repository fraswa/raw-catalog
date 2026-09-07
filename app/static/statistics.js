'use strict';
const $ = id => document.getElementById(id);
let csrf = '', archiveData = null, selectedCamera = '';
const nf = new Intl.NumberFormat();

async function api(url, options={}) {
  const response = await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});
  const result = await response.json();
  if(!response.ok){if(response.status===401) location.href='/'; throw new Error(result.error||`Request failed (${response.status})`);} return result;
}
function showError(err){$('statisticsError').textContent=err.message;$('statisticsError').hidden=false;}
function libraryUrl(filters){const p=new URLSearchParams(filters);return '/?'+p.toString();}
function emptyBars(host){const span=document.createElement('span');span.className='read-only';span.textContent='No metadata available';host.append(span);}
function pct(value,total){return total?Math.round(value*1000/total)/10:0;}
function coverageText(value,total,prefix){return `${prefix}: ${nf.format(value||0)} of ${nf.format(total||0)} photos (${pct(value||0,total||0)}%)`;}

function addReferenceHandlers(target,type,name){
  target.classList.add('has-reference');
  const mark=document.createElement('span');mark.className='reference-mark';mark.textContent='ⓘ';mark.setAttribute('aria-hidden','true');target.append(mark);
  target.addEventListener('mouseenter',()=>showReference(target,type,name));
  target.addEventListener('mouseleave',hideReference);
  target.addEventListener('focus',()=>showReference(target,type,name));
  target.addEventListener('blur',hideReference);
}
function fact(label,value){
  if(value===null||value===undefined||value==='')return null;
  const dt=document.createElement('dt');dt.textContent=label;
  const dd=document.createElement('dd');dd.textContent=String(value);
  return [dt,dd];
}
function firstValue(rows){return rows?.length?rows[0].value:null;}
function topValues(rows,limit=2){return (rows||[]).slice(0,limit).map(x=>x.value).join(', ')||null;}
function showReference(target,type,name){
  if(!archiveData)return;
  const detail=type==='camera'?archiveData.camera_breakdowns?.[name]:archiveData.lens_breakdowns?.[name];
  if(!detail)return;
  const card=$('referenceCard');$('referenceType').textContent=type==='camera'?'CAMERA · CATALOG REFERENCE':'LENS · CATALOG REFERENCE';$('referenceTitle').textContent=name;
  const facts=$('referenceFacts');facts.replaceChildren();
  const share=`${nf.format(detail.total_photos)} · ${pct(detail.total_photos,archiveData.total_photos)}% of archive`;
  const rows=type==='camera'?[
    fact('Maker',detail.maker||'Not reported'),
    fact('Usage',share),
    fact('Resolution',detail.megapixels||'Not reported'),
    fact('Sensor',detail.sensor_size||'Not reported'),
    fact('Lens mount',topValues(detail.mounts)||'Not reported'),
    fact('Lenses used',nf.format(detail.distinct_lenses||0)),
    fact('Most used lens',firstValue(detail.lenses)),
    fact('Active years',detail.active_years||'No capture dates')
  ]:[
    fact('Maker',detail.maker||'Not reported'),
    fact('Usage',share),
    fact('Lens mount',topValues(detail.mounts)||'Not reported'),
    fact('Cameras used',nf.format(detail.distinct_cameras||0)),
    fact('Most used camera',firstValue(detail.cameras)),
    fact('Common focal length',firstValue(detail.focal_lengths)),
    fact('Common aperture',firstValue(detail.apertures)),
    fact('Active years',detail.active_years||'No capture dates')
  ];
  for(const row of rows)if(row)facts.append(...row);
  $('referenceFooter').textContent='Derived from this catalog’s indexed EXIF metadata; it is not an external product database.';
  card.hidden=false;
  const rect=target.getBoundingClientRect();
  const width=Math.min(360,window.innerWidth-24);card.style.width=`${width}px`;
  const cardRect=card.getBoundingClientRect();
  let left=Math.min(rect.left,window.innerWidth-cardRect.width-12);left=Math.max(12,left);
  let top=rect.bottom+8;if(top+cardRect.height>window.innerHeight-12)top=Math.max(12,rect.top-cardRect.height-8);
  card.style.left=`${left}px`;card.style.top=`${top}px`;
}
function hideReference(){$('referenceCard').hidden=true;}

function renderBars(id, rows, options={}){
  const host=$(id);host.replaceChildren();const max=Math.max(1,...rows.map(r=>r.count));
  if(!rows.length){emptyBars(host);return;}
  for(const row of rows){
    const item=document.createElement('div');item.className='bar-item'+(options.camera&&row.value===selectedCamera?' selected':'');
    let label;
    if(options.camera){
      label=document.createElement('button');label.type='button';label.className='bar-filter-link';label.onclick=()=>selectCamera(row.value);
    } else if(options.libraryParam){
      label=document.createElement('a');const filters={[options.libraryParam]:row.value};if(options.includeCamera&&selectedCamera)filters.camera=selectedCamera;label.href=libraryUrl(filters);label.className='bar-link';
    } else {label=document.createElement('span');}
    const labelText=document.createElement('span');labelText.textContent=row.value;label.append(labelText);label.title=row.value;
    if(options.referenceType)addReferenceHandlers(label,options.referenceType,row.value);
    const bar=document.createElement('progress');bar.max=max;bar.value=row.count;
    const count=document.createElement('strong');
    const percentage=options.total?Math.round(row.count*1000/options.total)/10:null;
    count.textContent=percentage===null?nf.format(row.count):`${nf.format(row.count)} · ${percentage}%`;
    item.append(label,bar,count);
    if(options.camera){
      const open=document.createElement('a');open.className='mini-filter';open.href=libraryUrl({camera:row.value});open.textContent='Open Library';open.title=`Open ${row.value} photographs in Library`;item.append(open);
    }
    host.append(item);
  }
}
function renderTechnical(data){
  renderBars('makerBars',data.makers||[],{total:data.total_photos});
  renderBars('megapixelBars',data.megapixels||[],{total:data.megapixel_coverage||data.total_photos});
  renderBars('sensorBars',data.sensor_sizes||[],{total:data.sensor_coverage||data.total_photos});
  renderBars('mountBars',data.mounts||[],{total:data.mount_coverage||data.total_photos});
  $('makerCoverage').textContent=coverageText(data.maker_coverage,data.total_photos,'EXIF maker coverage');
  $('megapixelCoverage').textContent=coverageText(data.megapixel_coverage,data.total_photos,'Resolution coverage');
  $('sensorCoverage').textContent=coverageText(data.sensor_coverage,data.total_photos,'Sensor-size coverage');
  $('mountCoverage').textContent=coverageText(data.mount_coverage,data.total_photos,'Lens-mount coverage');
}
function svg(name,attrs={}){const node=document.createElementNS('http://www.w3.org/2000/svg',name);for(const[k,v]of Object.entries(attrs))node.setAttribute(k,String(v));return node;}
function renderTrendRows(rows,names){
  const chart=$('cameraTrend');chart.replaceChildren();$('trendLegend').replaceChildren();
  if(!rows.length||!names.length){$('trendDates').replaceChildren();return;}
  const left=45,right=980,top=20,bottom=285,width=right-left,height=bottom-top;
  let max=1;for(const row of rows)for(const name of names)max=Math.max(max,row.cameras[name]||0);
  for(let i=0;i<=4;i++){const y=top+height*i/4;chart.append(svg('line',{x1:left,y1:y,x2:right,y2:y,class:'chart-grid'}));const t=svg('text',{x:5,y:y+4,class:'chart-label'});t.textContent=nf.format(Math.round(max*(1-i/4)));chart.append(t);}
  names.forEach((name,index)=>{const points=rows.map((row,i)=>{const x=left+(rows.length===1?width/2:width*i/(rows.length-1));const y=bottom-height*((row.cameras[name]||0)/max);return `${x},${y}`;}).join(' ');chart.append(svg('polyline',{points,class:`trend-line line-${index}`}));const legend=document.createElement('button');legend.type='button';legend.onclick=()=>selectCamera(name);legend.className=`legend-item legend-${index}`;const swatch=document.createElement('span');swatch.className='legend-swatch';const text=document.createElement('span');text.textContent=name;legend.append(swatch,text);$('trendLegend').append(legend);});
  $('trendDates').replaceChildren();const first=document.createElement('span');first.textContent=rows[0].month;const last=document.createElement('span');last.textContent=rows[rows.length-1].month;$('trendDates').append(first,last);
}
function renderGlobalTrend(data){renderTrendRows(data.trend||[],data.top_camera_names||[]);}
function renderCameraTrend(camera,detail){
  const rows=(detail.trend||[]).map(row=>({month:row.month,cameras:{[camera]:row.count}}));
  renderTrendRows(rows,[camera]);
}
function renderSummary(data, detail=null){
  if(!detail){
    $('summaryLabel1').textContent='Photographs';$('totalPhotos').textContent=nf.format(data.total_photos);
    $('summaryLabel2').textContent='With capture date';$('datedPhotos').textContent=`${nf.format(data.dated_photos)} (${data.total_photos?Math.round(data.dated_photos*100/data.total_photos):0}%)`;
    $('summaryLabel3').textContent='Cameras';$('distinctCameras').textContent=nf.format(data.distinct_cameras);
    $('summaryLabel4').textContent='Lenses';$('distinctLenses').textContent=nf.format(data.distinct_lenses);return;
  }
  $('summaryLabel1').textContent='Camera photographs';$('totalPhotos').textContent=nf.format(detail.total_photos);
  $('summaryLabel2').textContent='Archive share';$('datedPhotos').textContent=`${data.total_photos?Math.round(detail.total_photos*1000/data.total_photos)/10:0}%`;
  $('summaryLabel3').textContent='With capture date';$('distinctCameras').textContent=`${nf.format(detail.dated_photos)} (${detail.total_photos?Math.round(detail.dated_photos*100/detail.total_photos):0}%)`;
  $('summaryLabel4').textContent='Lenses used';$('distinctLenses').textContent=nf.format(detail.distinct_lenses);
}
function selectCamera(camera){
  if(!archiveData||!archiveData.camera_breakdowns?.[camera])return;
  selectedCamera=camera;const detail=archiveData.camera_breakdowns[camera];hideReference();
  $('cameraScope').hidden=false;$('cameraScopeName').textContent=camera;$('cameraScopeSummary').textContent=`${nf.format(detail.total_photos)} photos · ${nf.format(detail.distinct_lenses)} lenses${detail.megapixels?' · '+detail.megapixels:''}${detail.sensor_size?' · '+detail.sensor_size:''}`;
  $('cameraScopeLibrary').href=libraryUrl({camera});
  $('lensHeading').textContent=`Lens usage — ${camera}`;$('focalHeading').textContent=`Focal lengths — ${camera}`;$('apertureHeading').textContent=`Apertures — ${camera}`;$('trendHeading').textContent=`${camera} usage — last 60 active months`;$('yearHeading').textContent=`${camera} photographs by year`;
  renderSummary(archiveData,detail);
  renderBars('cameraBars',archiveData.cameras,{camera:true,total:archiveData.total_photos,referenceType:'camera'});
  renderBars('lensBars',detail.lenses,{libraryParam:'lens',includeCamera:true,total:detail.total_photos,referenceType:'lens'});
  renderBars('focalBars',detail.focal_lengths,{total:detail.total_photos});
  renderBars('apertureBars',detail.apertures,{total:detail.total_photos});
  renderBars('yearBars',(detail.yearly||[]).map(x=>({value:x.year,count:x.count})).reverse(),{total:detail.dated_photos});
  renderCameraTrend(camera,detail);
}
function clearCamera(){
  selectedCamera='';hideReference();$('cameraScope').hidden=true;
  $('lensHeading').textContent='Lens usage';$('focalHeading').textContent='Most-used focal lengths';$('apertureHeading').textContent='Most-used apertures';$('trendHeading').textContent='Camera usage — last 60 active months';$('yearHeading').textContent='Photographs by year';
  show(archiveData,false);
}
function show(data, resetSelection=true){
  archiveData=data;if(resetSelection)selectedCamera='';hideReference();
  $('statisticsLoading').hidden=true;$('statisticsContent').hidden=false;$('statisticsError').hidden=true;$('cameraScope').hidden=true;
  renderSummary(data);$('generatedAt').textContent=`${data.cached?'Cached':'Calculated'} ${new Date(data.generated_at).toLocaleString()}`;
  renderBars('cameraBars',data.cameras,{camera:true,total:data.total_photos,referenceType:'camera'});
  renderBars('lensBars',data.lenses,{libraryParam:'lens',total:data.total_photos,referenceType:'lens'});
  renderBars('focalBars',data.focal_lengths,{total:data.total_photos});
  renderBars('apertureBars',data.apertures,{total:data.total_photos});
  renderTechnical(data);
  renderBars('yearBars',(data.yearly||[]).map(x=>({value:x.year,count:x.count})).reverse(),{total:data.dated_photos});renderGlobalTrend(data);
}
async function load(force=false){$('statisticsLoading').hidden=false;$('statisticsContent').hidden=true;$('refreshStats').disabled=true;try{show(await api('/api/statistics'+(force?'?refresh=1':'')));}catch(err){$('statisticsLoading').hidden=true;showError(err);}finally{$('refreshStats').disabled=false;}}
$('clearCameraScope').onclick=clearCamera;
$('refreshStats').onclick=()=>load(true);
$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});}finally{location.href='/';}};
window.addEventListener('scroll',hideReference,{passive:true});window.addEventListener('resize',hideReference);
(async()=>{try{const state=await api('/api/session');csrf=state.csrf;if(!state.authenticated){location.href='/';return;}$('statisticsApp').hidden=false;await load();}catch(err){$('statisticsApp').hidden=false;$('statisticsLoading').hidden=true;showError(err);}})();
