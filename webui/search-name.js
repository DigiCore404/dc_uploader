'use strict';
// Literal phrases only: configurable release tags are never interpreted as regex.
(()=>{
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
function normalize(value,words=[]){
 let title=String(value||'').trim();
 if(/^https?:\/\//i.test(title))return title;
 title=title.replace(/[._]+/g,' ');
 const terms=[...new Set(words.map(word=>String(word).trim()).filter(Boolean))].sort((a,b)=>b.length-a.length);
 for(const term of terms){
  const phrase=term.split(/[\s._-]+/).filter(Boolean).map(escape).join('[\\s._-]+');
  if(!phrase)continue;
  title=title.replace(new RegExp('(^|[^\\p{L}\\p{N}])'+phrase+'(?=$|[^\\p{L}\\p{N}])','giu'),'$1 ');
 }
 return title.replace(/\[\s*\]|\(\s*\)|\{\s*\}/g,' ').replace(/\s+/g,' ').replace(/^[\s-]+|[\s-]+$/g,'').trim();
}
window.SearchTitles={normalize};
})();
