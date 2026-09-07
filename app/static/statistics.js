'use strict';
const $ = id => document.getElementById(id);
let csrf = '';
const nf = new Intl.NumberFormat();
async function api(url, options={}) {
  const response = await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});
  const result = await response.json();
  if(!response.ok){if(response.status===401) location.href='/'; throw new Error(result.error||`Request failed (${response.status})`);} return result;
}
function showError(err){$('statisticsError').textContent=err.message;$('statisticsError').hidden=false;}
function renderBars(id, rows){
  const host=$(id);host.replaceChildren();const max=Math.max(1,...rows.map(r=>r.count));
  if(!rows.length){const span=document.createElement('span');span.className='read-only';span.textContent='No metadata available';host.append(span);return;}
  for(const row of rows){const item=document.createElement('div');item.className='bar-item';const label=document.createElement('span');label.textContent=row.value;label.title=row.value;const bar=document.createElement('progress');bar.max=max;bar.value=row.count;const count=document.createElement('strong');count.textContent=nf.format(row.count);item.append(label,bar,count);host.append(item);}
}
function svg(name,attrs={}){const node=document.createElementNS('http://www.w3.org/2000/svg',name);for(const[k,v]of Object.entries(attrs))node.setAttribute(k,String(v));return node;}
function renderTrend(data){
  const chart=$('cameraTrend');chart.replaceChildren();$('trendLegend').replaceChildren();
  const rows=data.trend||[], names=data.top_camera_names||[]; if(!rows.length||!names.length)return;
  const left=45,right=980,top=20,bottom=285,width=right-left,height=bottom-top;
  let max=1;for(const row of rows)for(const name of names)max=Math.max(max,row.cameras[name]||0);
  for(let i=0;i<=4;i++){const y=top+height*i/4;chart.append(svg('line',{x1:left,y1:y,x2:right,y2:y,class:'chart-grid'}));const t=svg('text',{x:5,y:y+4,class:'chart-label'});t.textContent=nf.format(Math.round(max*(1-i/4)));chart.append(t);}
  names.forEach((name,index)=>{const points=rows.map((row,i)=>{const x=left+(rows.length===1?width/2:width*i/(rows.length-1));const y=bottom-height*((row.cameras[name]||0)/max);return `${x},${y}`;}).join(' ');chart.append(svg('polyline',{points,class:`trend-line line-${index}`}));const legend=document.createElement('span');legend.className=`legend-item legend-${index}`;const swatch=document.createElement('span');swatch.className='legend-swatch';const text=document.createElement('span');text.textContent=name;legend.append(swatch,text);$('trendLegend').append(legend);});
  $('trendDates').replaceChildren();const first=document.createElement('span');first.textContent=rows[0].month;const last=document.createElement('span');last.textContent=rows[rows.length-1].month;$('trendDates').append(first,last);
}
function show(data){
  $('statisticsLoading').hidden=true;$('statisticsContent').hidden=false;$('statisticsError').hidden=true;
  $('totalPhotos').textContent=nf.format(data.total_photos);$('datedPhotos').textContent=`${nf.format(data.dated_photos)} (${data.total_photos?Math.round(data.dated_photos*100/data.total_photos):0}%)`;
  $('distinctCameras').textContent=nf.format(data.distinct_cameras);$('distinctLenses').textContent=nf.format(data.distinct_lenses);
  $('generatedAt').textContent=`${data.cached?'Cached':'Calculated'} ${new Date(data.generated_at).toLocaleString()}`;
  renderBars('cameraBars',data.cameras);renderBars('lensBars',data.lenses);renderBars('focalBars',data.focal_lengths);renderBars('apertureBars',data.apertures);
  renderBars('yearBars',(data.yearly||[]).map(x=>({value:x.year,count:x.count})).reverse());renderTrend(data);
}
async function load(force=false){$('statisticsLoading').hidden=false;$('statisticsContent').hidden=true;$('refreshStats').disabled=true;try{show(await api('/api/statistics'+(force?'?refresh=1':'')));}catch(err){$('statisticsLoading').hidden=true;showError(err);}finally{$('refreshStats').disabled=false;}}
$('refreshStats').onclick=()=>load(true);
$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});}finally{location.href='/';}};
(async()=>{try{const state=await api('/api/session');csrf=state.csrf;if(!state.authenticated){location.href='/';return;}$('statisticsApp').hidden=false;await load();}catch(err){$('statisticsApp').hidden=false;$('statisticsLoading').hidden=true;showError(err);}})();
