import { useState } from 'react'
import { Search, Download, ExternalLink, Box, LoaderCircle, Check, AlertCircle } from 'lucide-react'
import type { Model, SearchResponse, SourceResult } from './types'

const sourceOrder=['printables','makerworld','thingiverse','grabcad']
const niceName:Record<string,string>={printables:'Printables',makerworld:'MakerWorld',thingiverse:'Thingiverse',grabcad:'GrabCAD'}

export default function App(){
  const [query,setQuery]=useState(''); const [response,setResponse]=useState<SearchResponse|null>(null)
  const [busy,setBusy]=useState(false); const [error,setError]=useState(''); const [selected,setSelected]=useState<Model[]>([])
  const [job,setJob]=useState<{status:string;progress:number;message:string;project_path?:string}|null>(null)
  const [retrying,setRetrying]=useState<string|null>(null)
  async function search(e?:React.FormEvent){e?.preventDefault(); if(!query.trim()||busy)return; setBusy(true);setError('');setSelected([]);setJob(null)
    try{const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:query.trim()})});if(!r.ok)throw new Error(`Search failed (${r.status})`);setResponse(await r.json())}catch(e){setError(e instanceof Error?e.message:'Unable to search')}finally{setBusy(false)}}
  function toggle(m:Model){setSelected(old=>old.some(x=>x.source===m.source&&x.id===m.id)?old.filter(x=>!(x.source===m.source&&x.id===m.id)):old.length<5?[...old,m]:old)}
  async function download(){if(!response||!selected.length)return;setJob({status:'queued',progress:0,message:'Starting download…'});setError('')
    try{const r=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:response.query,models:selected.map(m=>({source:m.source,model_id:m.id,model_url:m.model_url}))})});if(!r.ok)throw new Error((await r.json()).detail||'Could not start project download');const initial=await r.json();let done=false
      while(!done){const p=await fetch(`/api/projects/${initial.job_id}`);const state=await p.json();setJob(state);done=['complete','partial','failed'].includes(state.status);if(!done)await new Promise(resolve=>setTimeout(resolve,900))}
    }catch(e){setError(e instanceof Error?e.message:'Download failed')}}
  async function retrySource(source:string){if(!response)return;setRetrying(source)
    try{const r=await fetch('/api/search/source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:response.query,source})});if(!r.ok)throw new Error(`Retry failed (${r.status})`);const updated:SourceResult=await r.json();setResponse(old=>old?{...old,total_results:old.total_results-(old.sources.find(s=>s.source===source)?.results.length||0)+updated.results.length,sources:old.sources.map(s=>s.source===source?updated:s)}:old)}catch(e){setError(e instanceof Error?e.message:'Source retry failed')}finally{setRetrying(null)}}
  const groups=sourceOrder.map(key=>response?.sources.find(x=>x.source===key)).filter(Boolean) as SourceResult[]
  return <main className="shell">
    <header className="top"><a className="brand" href="#"><span className="brandIcon"><Box size={20}/></span>FORM<span className="brandDim">FINDER</span></a><span className="local">LOCAL WORKSPACE</span></header>
    <section className="hero"><div className="eyebrow">YOUR NEXT PRINT STARTS HERE</div><h1>Find a model.<br/><em>Make it yours.</em></h1><p>Search across the 3D maker community and collect models in one organized project.</p>
      <form className="search" onSubmit={search}><Search size={19}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search for a model…" aria-label="Search for a model"/><button disabled={busy||!query.trim()}>{busy?<LoaderCircle className="spin" size={17}/>:null}{busy?'Searching':'Search models'}</button></form>
      {error&&<div className="error"><AlertCircle size={16}/>{error}</div>}
    </section>
    {response&&<section className="results"><div className="resultsHead"><div><div className="eyebrow">COMMUNITY RESULTS</div><h2>“{response.query}” <span>{response.total_results} found</span></h2></div><div className="downloadArea"><span className="counter">Selected <b>{selected.length}</b> / 5</span><button className="downloadBtn" onClick={download} disabled={!selected.length||!!job&&job.status!=='failed'}><Download size={16}/>{job&&['queued','running'].includes(job.status)?'Downloading…':`Download selected (${selected.length})`}</button></div></div>
      {job&&<div className={`job ${job.status}`}><div className="jobText">{job.status==='complete'?<Check size={16}/>:job.status==='failed'?<AlertCircle size={16}/>:<LoaderCircle size={16} className="spin"/>}{job.message}{job.project_path&&<span> · {job.project_path}</span>}</div><div className="progress"><i style={{width:`${job.progress}%`}}/></div></div>}
      {groups.map(group=><section className="source" key={group.source}><div className="sourceHead"><h3>{niceName[group.source]||group.label}</h3><div className="sourceActions">{['success','success_empty'].includes(group.status)?<span className="status">{group.status==='success_empty'?'0 RESULTS':`${group.results.length} RESULTS`}</span>:<><span className="status bad">{group.status.replace(/_/g,' ').toUpperCase()}</span><button className="retry" onClick={()=>retrySource(group.source)} disabled={retrying!==null}>{retrying===group.source?<LoaderCircle size={13} className="spin"/>:'Retry'}</button></>}</div></div>
        {group.status==='success_empty'?<div className="sourceMessage">No models found for this search.</div>:!['success'].includes(group.status)?<div className="sourceMessage"><strong>Unable to access {niceName[group.source]||group.label}.</strong><br/>{group.error?.message||'The source is unavailable right now.'}<div className="unavailableActions"><button className="retry" onClick={()=>retrySource(group.source)} disabled={retrying!==null}>{retrying===group.source?'Retrying…':'Retry source'}</button><a href={sourceWebsite(group.source)} target="_blank" rel="noreferrer">Open website ↗</a></div></div>:<div className="grid">{group.results.map(model=><Card key={`${model.source}-${model.id}`} model={model} chosen={selected.some(x=>x.source===model.source&&x.id===model.id)} disabled={!selected.some(x=>x.source===model.source&&x.id===model.id)&&selected.length>=5} onToggle={()=>toggle(model)}/>)}</div>}
      </section>)}
    </section>}
    {!response&&!busy&&<section className="below"><div className="belowIcon"><Search size={22}/></div><span>FOUR COMMUNITIES · ONE SEARCH</span><p>Printables, MakerWorld, Thingiverse, and GrabCAD Community.</p></section>}
    <footer><span>FORMFINDER <i>·</i> PERSONAL MAKER TOOL</span><span>Respect each model’s license and source attribution.</span></footer>
  </main>
}

function sourceWebsite(source:string){return ({printables:'https://www.printables.com',makerworld:'https://makerworld.com',thingiverse:'https://www.thingiverse.com',grabcad:'https://grabcad.com/library'} as Record<string,string>)[source]||'https://example.com'}

function Card({model,chosen,disabled,onToggle}:{model:Model;chosen:boolean;disabled:boolean;onToggle:()=>void}){
  const popularity=model.downloads!=null?`${model.downloads.toLocaleString()} downloads`:model.likes!=null?`${model.likes.toLocaleString()} likes`:model.favorites!=null?`${model.favorites.toLocaleString()} saves`:null
  const files=[...new Set(model.available_files.map(f=>f.category).filter(x=>x!=='OTHER'))]
  return <article className={`card ${chosen?'chosen':''}`}><div className="thumb">{model.thumbnail_url?<img src={model.thumbnail_url} loading="lazy" onError={e=>{e.currentTarget.style.display='none'}}/>:<div className="placeholder"><Box size={29}/></div>}<label className={`select ${chosen?'checked':''}`} title={disabled?'Maximum of 5 selected':'Select model'}><input type="checkbox" checked={chosen} disabled={disabled} onChange={onToggle}/>{chosen?<Check size={15}/>:null}</label><span className="sourceTag">{niceName[model.source]}</span></div>
    <div className="cardBody"><h4 title={model.title}>{model.title}</h4><div className="byline">{model.author?<>by <b>{model.author}</b></>:'Creator not listed'}</div><div className="stats">{popularity&&<span>{popularity}</span>}{model.rating!=null&&<span>★ {model.rating.toFixed(1)}</span>}{!popularity&&model.rating==null&&<span>Community model</span>}</div>
      <div className="fileRow">{files.length?files.map(f=><span key={f} className="fileChip">{f}</span>):<span className="muted">Files shown on model page</span>}</div>
      <a className="original" href={model.model_url} target="_blank" rel="noreferrer">View original <ExternalLink size={13}/></a>
    </div></article>
}
