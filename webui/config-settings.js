'use strict';
function renderConfigSettings(settings){
 const target=document.getElementById('config-settings');if(!target)return;
 const open=new Set(Array.from(target.querySelectorAll('details[open]')).map(x=>x.dataset.section));const first=!target.children.length;target.replaceChildren();
 const intro=element('div','config-intro');intro.append(element('h2','','Uploader configuration'),element('p','','These controls save directly to '+settings.config_file+'. Both the web service and CLI use this file. Client connections and workspace access have their own controls.'));target.append(intro);
 for(const section of settings.config_schema||[]){
  const details=element('details','card config-section');details.dataset.section=section.section;details.open=open.has(section.section)||(first&&section.section==='MediaTools');
  const summary=element('summary','',section.title);details.append(summary);const grid=element('div','config-grid');
  for(const field of section.fields){
   const wrapper=element('div','config-field'),id='cfg-'+section.section+'-'+field.key;const label=element('label','',field.label);label.htmlFor=id;
   const input=element('input');input.id=id;input.dataset.configSection=section.section;input.dataset.configKey=field.key;input.dataset.configType=field.type;
   input.type={boolean:'checkbox',number:'number',secret:'password'}[field.type]||'text';input.disabled=field.locked;
   if(field.type==='boolean')input.checked=String(field.value).toLowerCase()==='true';else input.value=field.value;
   input.dataset.original=field.type==='boolean'?String(input.checked):input.value;
   if(field.type==='secret'){input.autocomplete='new-password';input.placeholder=field.saved?'Saved — leave blank to keep':'Not configured';}
   if(field.range){input.min=field.range[0];input.max=field.range[1];input.step=field.key==='ratio_target'?'0.1':'1';}
   const help=element('small','field-help',field.help+(field.locked?' Managed by Docker; change the container mount.':''));
   if(field.type==='boolean'){const toggle=element('div','config-toggle');toggle.append(label,input);wrapper.append(toggle,help);}else wrapper.append(label,input,help);
   const mapping=element('code','config-key',section.section+'.'+field.key.toUpperCase());wrapper.append(mapping);
   if(field.key==='mtnsetting'){
    const controls=element('div','mtn-grid-controls');
    for(const [flag,name,fallback] of [['c','Columns',2],['r','Rows',8]]){
     const box=element('div'),gridLabel=element('label','',name),number=element('input');number.type='number';number.min=1;number.max=20;number.id=id+'-'+flag;gridLabel.htmlFor=number.id;
     const match=input.value.match(new RegExp('-'+flag+'\\s+(\\d+)'));number.value=match?match[1]:fallback;
     number.addEventListener('input',()=>{const expression=new RegExp('-'+flag+'\\s+\\d+');input.value=expression.test(input.value)?input.value.replace(expression,'-'+flag+' '+number.value):input.value+' -'+flag+' '+number.value;});
     box.append(gridLabel,number);controls.append(box);
    }
    wrapper.insertBefore(controls,label);input.setAttribute('aria-label','Advanced MTN layout flags');label.textContent='Advanced MTN layout flags';
   }
   grid.append(wrapper);
  }
  details.append(grid);target.append(details);
 }
 const save=element('div','settings-savebar'),button=element('button','button primary','Save uploader settings');button.type='submit';save.append(element('span','','One configuration for WebUI and CLI.'),button);target.append(save);
}
function collectConfigSettings(){
 const changes={};for(const input of document.querySelectorAll('#config-settings [data-config-key]')){
  if(input.disabled)continue;const value=input.type==='checkbox'?String(input.checked):input.value;
  if(value===input.dataset.original||input.dataset.configType==='secret'&&!value)continue;
  const section=input.dataset.configSection;(changes[section]||={})[input.dataset.configKey]=value;
 }return changes;
}
