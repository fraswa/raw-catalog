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
function renderBars(id, rows, options={}){
  const host=$(id);host.replaceChildren();const max=Math.max(1,...rows.map(r=>r.count));
  if(!rows.length){emptyBars(host);return;}
  for(const row of rows){
    const item=document.createElement('div');item.className='bar-item'+(options.camera&&row.value===selectedCamera?' selected':'');
    let label;
    if(options.libraryParam){label=document.createElement('a');label.href=libraryUrl({[options.libraryParam]:row.value});label.className='bar-link';}
    else {label=document.createElement('span');}
    label.textContent=row.value;label.title=row.value;
    const bar=document.createElement('progress');bar.max=max;bar.value=row.count;
    const count=document.createElement('strong');count.textContent=nf.format(row.count);
    item.append(label,bar,count);
    if(options.camera){
      const filter=document.createElement('button');filter.type='button';filter.className='mini-filter';filter.textContent=row.value===selectedCamera?'Selected':'Filter stats';filter.disabled=row.value===selectedCamera;
      filter.onclick=()=>selectCamera(row.value);item.append(filter);
    }
    host.append(item);
  }
}
function svg(name,attrs={}){const node=document.createElementNS('http://www.w3.org/2000/svg',name);for(const[k,v]of Object.entries(attrs))node.setAttribute(k,String(v));return node;}
function renderTrendRows(rows,names){
  const chart=$('cameraTrend');chart.replaceChildren();$('trendLegend').replaceChildren();
  if(!rows.length||!names.length){$('trendDates').replaceChildren();return;}
  const left=45,right=980,top=20,bottom=285,width=right-left,height=bottom-top;
  let max=1;for(const row of rows)for(const name of names)max=Math.max(max,row.cameras[name]||0);
  for(let i=0;i<=4;i++){const y=top+height*i/4;chart.append(svg('line',{x1:left,y1:y,x2:right,y2:y,class:'chart-grid'}));const t=svg('text',{x:5,y:y+4,class:'chart-label'});t.textContent=nf.format(Math.round(max*(1-i/4)));chart.append(t);}
  names.forEach((name,index)=>{const points=rows.map((row,i)=>{const x=left+(rows.length===1?width/2:width*i/(rows.length-1));const y=bottom-height*((row.cameras[name]||0)/max);return `${x},${y}`;}).join(' ');chart.append(svg('polyline',{points,class:`trend-line line-${index}`}));const legend=document.createElement('a');legend.href=libraryUrl({camera:name});legend.className=`legend-item legend-${index}`;const swatch=document.createElement('span');swatch.className='legend-swatch';const text=document.createElement('span');text.textContent=name;legend.append(swatch,text);$('trendLegend').append(legend);});
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
  selectedCamera=camera;const detail=archiveData.camera_breakdowns[camera];
  $('cameraScope').hidden=false;$('cameraScopeName').textContent=camera;$('cameraScopeSummary').textContent=`${nf.format(detail.total_photos)} photos · ${nf.format(detail.distinct_lenses)} lenses`;
  $('cameraScopeLibrary').href=libraryUrl({camera});
  $('lensHeading').textContent=`Lens usage — ${camera}`;$('focalHeading').textContent=`Focal lengths — ${camera}`;$('apertureHeading').textContent=`Apertures — ${camera}`;$('trendHeading').textContent=`${camera} usage — last 60 active months`;$('yearHeading').textContent=`${camera} photographs by year`;
  renderSummary(archiveData,detail);renderBars('cameraBars',archiveData.cameras,{camera:true,libraryParam:'camera'});renderBars('lensBars',detail.lenses,{libraryParam:'lens'});renderBars('focalBars',detail.focal_lengths);renderBars('apertureBars',detail.apertures);renderBars('yearBars',(detail.yearly||[]).map(x=>({value:x.year,count:x.count})).reverse());renderCameraTrend(camera,detail);
}
function clearCamera(){
  selectedCamera='';$('cameraScope').hidden=true;
  $('lensHeading').textContent='Lens usage';$('focalHeading').textContent='Most-used focal lengths';$('apertureHeading').textContent='Most-used apertures';$('trendHeading').textContent='Camera usage — last 60 active months';$('yearHeading').textContent='Photographs by year';
  show(archiveData,false);
}
function show(data, resetSelection=true){
  archiveData=data;if(resetSelection)selectedCamera='';
  $('statisticsLoading').hidden=true;$('statisticsContent').hidden=false;$('statisticsError').hidden=true;$('cameraScope').hidden=true;
  renderSummary(data);$('generatedAt').textContent=`${data.cached?'Cached':'Calculated'} ${new Date(data.generated_at).toLocaleString()}`;
  renderBars('cameraBars',data.cameras,{camera:true,libraryParam:'camera'});renderBars('lensBars',data.lenses,{libraryParam:'lens'});renderBars('focalBars',data.focal_lengths);renderBars('apertureBars',data.apertures);
  renderBars('yearBars',(data.yearly||[]).map(x=>({value:x.year,count:x.count})).reverse());renderGlobalTrend(data);
}
async function load(force=false){$('statisticsLoading').hidden=false;$('statisticsContent').hidden=true;$('refreshStats').disabled=true;try{show(await api('/api/statistics'+(force?'?refresh=1':'')));}catch(err){$('statisticsLoading').hidden=true;showError(err);}finally{$('refreshStats').disabled=false;}}
$('clearCameraScope').onclick=clearCamera;
$('refreshStats').onclick=()=>load(true);
$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});}finally{location.href='/';}};
(async()=>{try{const state=await api('/api/session');csrf=state.csrf;if(!state.authenticated){location.href='/';return;}$('statisticsApp').hidden=false;await load();}catch(err){$('statisticsApp').hidden=false;$('statisticsLoading').hidden=true;showError(err);}})();
