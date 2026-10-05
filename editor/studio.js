const $=id=>document.getElementById(id);
let project=null, selected=0, view='book', timer, chain=Promise.resolve(), revision=0, saved=0, duplicate=false;
const pageFields=['title','date','phase','layout','caption'];
const designDefaults={dark:'#13315c',accent:'#dc0526',line:'#d3b59f',paper:'#ffffff',font:'',image_shape:'square',marker_shape:'circle',size:'a4-landscape',overview:'yes',rail:'dates',lang:'en',subtitle:'',author:''};
function notify(e){$('error').textContent=e.message||String(e);$('error').hidden=false;$('status').textContent='Needs attention';}
$('error').onclick=()=>$('error').hidden=true;
async function api(action,data){const response=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error);return result;}
async function get(url){const r=await fetch(url);const d=await r.json();if(!r.ok)throw Error(d.error);return d;}
function cover(){return project?.pages.find(p=>p.kind==='cover');}
function focusPreview(){
 if(!project)return;
 const doc=$('preview').contentDocument,p=project.pages[selected];if(!doc||!p)return;
 const i=project.pages.filter(p=>p.kind==='page').indexOf(p);
 const target=view==='book'?(p.kind==='page'?doc.querySelectorAll('.sheet.page')[i]:doc.querySelector('.sheet.'+p.kind)):(p.kind==='page'?doc.querySelectorAll('.tl-item.text')[i]:doc.querySelector(p.kind==='cover'?'.hero':'.closing'));
 target?.scrollIntoView({block:'start'});
}
function fitBook(){
 const doc=$('preview').contentDocument;if(!doc||view!=='book')return;
 let style=doc.getElementById('studio-fit');if(!style){style=doc.createElement('style');style.id='studio-fit';doc.head.append(style);}
 const widths={'a4-landscape':297,a4:210,'a5-landscape':210,square:210,'letter-landscape':279.4,letter:215.9};
 const zoom=Math.min(1,($('preview').clientWidth-32)/((widths[cover()?.meta.size]||297)*96/25.4));
 style.textContent='@media screen{.toolbar{display:none}body{padding:16px 0}.book{zoom:'+zoom+'}}';
}
$('preview').addEventListener('load',()=>{fitBook();focusPreview();});
new ResizeObserver(()=>fitBook()).observe($('preview'));
function preview(){if(!project)return;const base='/files/'+encodeURIComponent(project.id)+'/_output/';const url=base+(view==='book'?'preview.html':'index.html');$('preview').src=url+'?v='+Date.now();$('openPreview').href=url;$('empty').hidden=true;$('previewLabel').textContent=view==='book'?'Book preview':'Website preview';}
function schedule(){revision++;$('status').textContent='Unsaved changes';clearTimeout(timer);timer=setTimeout(()=>flush().catch(notify),1000);}
function flush(){clearTimeout(timer);chain=chain.catch(()=>{}).then(async()=>{if(!project||saved===revision)return;const id=project.id,version=revision;const pages=JSON.parse(JSON.stringify(project.pages));$('status').textContent='Saving & building…';await api('save',{project:id,pages});if(project?.id===id){saved=version;$('status').textContent=saved===revision?'All changes saved':'Unsaved changes';preview();}});return chain;}
function listPages(){const nav=$('pages');nav.replaceChildren();project.pages.forEach((p,i)=>{const b=document.createElement('button');b.className='page-button'+(i===selected?' active':'');const n=document.createElement('span');n.className='number';n.textContent=p.kind==='cover'?'▧':p.kind==='back'?'↳':i;const t=document.createElement('span');t.textContent=p.meta.title||'Untitled page';b.append(n,t);b.onclick=()=>{selected=i;render();focusPreview();};nav.append(b);});}
function render(){if(!project)return;selected=Math.min(selected,project.pages.length-1);const p=project.pages[selected];$('projectTitle').textContent=cover()?.meta.title||project.id;listPages();for(const key of pageFields)$(key).value=p.meta[key]||(key==='layout'?'auto':'');$('body').value=p.body;$('pageKind').textContent=p.kind==='cover'?'COVER DETAILS':p.kind==='back'?'CLOSING PAGE':'PAGE DETAILS';for(const [key,val]of Object.entries(designDefaults))$(key).value=cover()?.meta[key]??val;$('photos').replaceChildren();for(const name of p.images){const img=document.createElement('img');img.src='/files/'+[project.id,p.id,name].map(encodeURIComponent).join('/');img.alt=name;img.title=name;$('photos').append(img);}$('moveUp').disabled=p.kind!=='page'||project.pages.filter(p=>p.kind==='page').indexOf(p)===0;$('moveDown').disabled=p.kind!=='page'||project.pages.filter(p=>p.kind==='page').at(-1)===p;}
for(const key of pageFields)$(key).addEventListener('input',()=>{if(!project)return;project.pages[selected].meta[key]=$(key).value;if(key==='title'){listPages();$('projectTitle').textContent=cover()?.meta.title||project.id;}schedule();});
$('body').addEventListener('input',()=>{if(project){project.pages[selected].body=$('body').value;schedule();}});
for(const key of Object.keys(designDefaults))$(key).addEventListener('input',()=>{if(!cover())return;cover().meta[key]=$(key).value;$('preset').value='';schedule();});
const presets={heritage:{dark:'#13315c',accent:'#dc0526',line:'#d3b59f',paper:'#ffffff',image_shape:'square',marker_shape:'circle'},forest:{dark:'#294735',accent:'#7f993e',line:'#a7b593',paper:'#f7f6ee',image_shape:'rounded',marker_shape:'circle'},editorial:{dark:'#202020',accent:'#c43c30',line:'#b7b4ae',paper:'#faf9f6',image_shape:'square',marker_shape:'square'},coastal:{dark:'#214d69',accent:'#219b97',line:'#96c5cc',paper:'#f3fafb',image_shape:'pill',marker_shape:'rounded'}};
$('preset').onchange=()=>{const name=$('preset').value;if(!cover()||!presets[name])return;Object.assign(cover().meta,presets[name]);render();$('preset').value=name;schedule();};
function tab(design){$('contentPanel').hidden=design;$('designPanel').hidden=!design;$('contentTab').classList.toggle('active',!design);$('designTab').classList.toggle('active',design);}
$('contentTab').onclick=()=>tab(false);$('designTab').onclick=()=>tab(true);
$('bookView').onclick=()=>{view='book';$('bookView').classList.add('active');$('siteView').classList.remove('active');preview();};
$('siteView').onclick=()=>{view='site';$('siteView').classList.add('active');$('bookView').classList.remove('active');preview();};
for(const mode of ['desktop','mobile'])$(mode+'View').onclick=()=>{$('preview').classList.toggle('mobile',mode==='mobile');$('desktopView').classList.toggle('active',mode==='desktop');$('mobileView').classList.toggle('active',mode==='mobile');};
async function locked(fn){const elements=[...document.querySelectorAll('button,select,input,textarea')],states=elements.map(e=>e.disabled);elements.forEach(e=>e.disabled=true);try{await fn();}catch(e){notify(e);}finally{elements.forEach((e,i)=>e.disabled=states[i]);if(project)render();}}
async function projectList(id){const items=await get('/api/projects');$('project').replaceChildren();for(const item of items){const opt=document.createElement('option');opt.value=item.id;opt.textContent=item.title;$('project').append(opt);}if(id)$('project').value=id;return items;}
async function load(id){await flush();project=await get('/api/project?id='+encodeURIComponent(id));selected=0;revision=saved=0;render();$('status').textContent='Building preview…';await api('preview',{project:id});preview();$('status').textContent='All changes saved';}
$('project').onchange=()=>locked(()=>load($('project').value));
$('save').onclick=()=>locked(async()=>{await flush();if(project){await api('preview',{project:project.id});preview();}});
$('addPage').onclick=()=>locked(async()=>{if(!project)return;await flush();project=await api('add',{project:project.id});selected=project.pages.findLastIndex(p=>p.kind==='page');render();await api('preview',{project:project.id});preview();});
async function move(delta){if(!project)return;await flush();const pages=project.pages.filter(p=>p.kind==='page'),i=pages.indexOf(project.pages[selected]),j=i+delta;if(i<0||j<0||j>=pages.length)return;[pages[i],pages[j]]=[pages[j],pages[i]];project=await api('reorder',{project:project.id,order:pages.map(p=>p.id)});selected=project.pages.findIndex(p=>p.kind==='page')+j;render();await api('preview',{project:project.id});preview();}
$('moveUp').onclick=()=>locked(()=>move(-1));$('moveDown').onclick=()=>locked(()=>move(1));
function openCreate(copy){duplicate=copy;$('dialogTitle').textContent=copy?'Duplicate project':'Create a project';$('newName').value=copy?project.id+' copy':'';$('createDialog').showModal();$('newName').focus();}
$('newProject').onclick=()=>openCreate(false);$('duplicate').onclick=()=>{if(project)openCreate(true);};$('cancelCreate').onclick=()=>$('createDialog').close();
$('createForm').onsubmit=e=>{e.preventDefault();const name=$('newName').value.trim();if(!name)return;locked(async()=>{await flush();const data=await api('create',{name,source:duplicate?project?.id:null});$('createDialog').close();await projectList(data.id);await load(data.id);});};
$('upload').onchange=()=>{const files=[...$('upload').files];locked(async()=>{if(!project)return;await flush();const id=project.id,page=project.pages[selected].id;for(const file of files){if(file.size>20*1024*1024)throw Error('Each image must be smaller than 20 MB');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});project=await api('upload',{project:id,page,name:file.name,data});}render();await api('preview',{project:id});preview();});$('upload').value='';};
for(const [id,action]of [['exportPdf','pdf'],['exportSite','website']])$(id).onclick=()=>locked(async()=>{if(!project)return;await flush();$('status').textContent=action==='pdf'?'Rendering PDF…':'Packaging website…';const result=await api(action,{project:project.id});const a=document.createElement('a');a.href=result.url;a.download=action==='pdf'?'book.pdf':'website.zip';document.body.append(a);a.click();a.remove();$('status').textContent='Export ready';});
window.addEventListener('beforeunload',e=>{if(saved!==revision){e.preventDefault();e.returnValue='';}});
locked(async()=>{const items=await projectList();if(items.length)await load(items[0].id);});
