'use strict';
const CatalogMetadata={
 sites:[], search:async()=>{throw new Error('Choose TMDB Movies or TV.');},
 sourceLabel:row=>row.site||'TMDB',
 sameRelease:(a,b)=>a.id===b.id&&a.kind===b.kind,
 combine(rows){
  const first=rows[0]||{metadata:{},images:[]};
  const result={...first,metadata:{...first.metadata},images:[...(first.images||[])],choices:{},fieldSources:{},imageSources:{}};
  for(const [key,value] of Object.entries({...result.metadata,title:result.title,description:result.description})){
   result.choices[key]=value?[String(value)]:[];result.fieldSources[key]=[first.site||'Metadata'];
  }
  for(const url of result.images)result.imageSources[url]=[first.site||'Metadata'];
  return result;
 }
};
if(typeof module!=='undefined')module.exports=CatalogMetadata;
