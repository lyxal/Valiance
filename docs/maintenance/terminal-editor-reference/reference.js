'use strict';
// UX fixtures only. This is not a Valiance lexer, analyser, or runtime.
const $ = id => document.getElementById(id);
const terminal = $('terminal');
const shapeSource = [
  'import {std.math.\\PI}', 'trait Shape =>', '  extend perimeter -> Real', '  extend area -> Real', 'end', '',
  'object Circle => $radius: Real', '', 'object Circle as Shape =>',
  '  define perimeter => 2 * \\PI * $self.radius', '  define area => $self.radius ** 2 * \\PI', 'end', '',
  '#? Executed if ran directly', '$circle = Circle 10', 'println perimeter $circle', '$circle', '$circle | area | 5 * | 2 /', '', ''
].join('\n');
const worldSource = '#? Design fixture: three surviving overload worlds\ndefine scale =>\n  * 2\n  dup\nend\n\nscale 5\n';
const fixtures = {
  'Main.vlnc': 'import {Shapes.\\Circle}\n\n$circle = Circle 10\nprintln perimeter $circle\n$circle | area\n',
  'Shapes.vlnc': shapeSource,
  'MathHelpers.vlnc': '#? Helpers for the example\ndefine double => * 2 end\ndefine square => dup | * end\n\ndouble 5\n',
  'Overloads.vlnc': worldSource
};
const catalog = [
  {name:'**',signatures:['Real, Real → Real','Int, Int → Real','Number, Number → Number'],type:'Number',doc:'Raise one number to the power of another.'},
  {name:'*',signatures:['Real, Real → Real','Int, Int → Int','Number, Number → Number'],type:'Number',doc:'Multiply two numbers.'},
  {name:'/',signatures:['Int, Int → Real','Real, Real → Real'],type:'Number',doc:'Divide two numbers.'},
  {name:'+',signatures:['Int, Int → Int','Real, Real → Real'],type:'Number',doc:'Add two numbers.'},
  {name:'Circle',signatures:['Real → Circle'],type:'Number',doc:'Construct a Circle with the given radius.'},
  {name:'area',signatures:['Circle → Real'],type:'Circle',doc:'Return the area of a shape.'},
  {name:'perimeter',signatures:['Circle → Real'],type:'Circle',doc:'Return the perimeter of a shape.'},
  {name:'dup',signatures:['T → T, T'],doc:'Duplicate the top stack value.'},
  {name:'drop',signatures:['T →'],doc:'Discard the top stack value.'},
  {name:'swap',signatures:['A, B → B, A'],doc:'Exchange the top two values.'},
  {name:'println',signatures:['T →'],doc:'Print a value followed by a newline.'},
  {name:'concat',signatures:['String, String → String','[T], [T] → [T]'],type:'String',doc:'Concatenate compatible values.'},
  ...['import','define','object','trait','extend','end','if','else','while','return'].map(name=>({name,signatures:[],doc:'Language keyword'}))
];
const roles=['background','text','gutter','tabs','active-tab','inactive-tab','divider','keyword','type','number','comment','error','output','status','focus','dim','state-background','repl-background'];
const defaults=Object.fromEntries(roles.map(role=>[role,getComputedStyle(document.documentElement).getPropertyValue('--'+role).trim()]));
let docs=[],active=0,untitledCount=0,locked=false,selectedError=null,pinned=false;
let inspectorView='cursor',currentContext=null,lastContextKey='',analysisTimer,renderFrame;
let loaded=null,running=false,runTimer,demoStack=[],transcript=[],dropped=0;
let history=[],historyIndex=0,savedDraft='',completion=null;
const worlds=new Map();
try {history=JSON.parse(localStorage.getItem('valiance-design-history')||'[]');if(!Array.isArray(history))history=[];}catch{history=[];}
historyIndex=history.length;
const doc=()=>docs[active];
const esc=text=>String(text).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function makeDoc(name,text='',path=null){return {id:crypto.randomUUID(),name,path,text,saved:text,revision:1,caret:0,end:0,scrollTop:0,scrollLeft:0,undo:[],redo:[]};}
function addDoc(name,text='',path=null){docs.push(makeDoc(name,text,path));activate(docs.length-1);}
function notice(text){$('notice').textContent=text;}
function renderTabs(){
  $('file-tabs').replaceChildren();
  docs.forEach((d,index)=>{
    const group=document.createElement('div');group.className='tab-group'+(index===active?' active':'');
    const b=document.createElement('button');b.className='file-tab';b.setAttribute('role','tab');b.setAttribute('aria-selected',String(index===active));
    const duplicate=docs.filter(x=>x.name===d.name).length>1;
    b.textContent=(duplicate?(d.path||d.name+' · opened file '+(index+1)):d.name)+(d.text!==d.saved?' *':'');b.title=(d.path||d.name)+' · Double-click to rename';b.onclick=()=>activate(index);b.ondblclick=()=>renameDoc(index);group.append(b);
    for(const [label,glyph,action] of [['Close','×',()=>closeDoc(index)]]){const control=document.createElement('button');control.className='tab-action';control.textContent=glyph;control.title=label+' '+d.name;control.setAttribute('aria-label',control.title);control.onclick=action;group.append(control);}
    $('file-tabs').append(group);
  });
}
function rememberPosition(){if(doc())Object.assign(doc(),{caret:$('source').selectionStart,end:$('source').selectionEnd,scrollTop:$('source').scrollTop,scrollLeft:$('source').scrollLeft});}
function activate(index,remember=true){
  if(remember)rememberPosition();active=index;selectedError=null;pinned=false;inspectorView='cursor';hideCompletion();
  $('source').value=doc().text;$('source').setSelectionRange(doc().caret,doc().end);$('source').scrollTop=doc().scrollTop;$('source').scrollLeft=doc().scrollLeft;
  renderTabs();renderSource();updateCursor(true);updateLoaded();
}
function diagnostics(d=doc()){
  const result=[];
  for(const [pattern,message,help] of [
    [/\bconcat\b/g,'No matching overload for concat with Real, Real.','Use a numeric operation for these operands.'],
    [/\bCircel\b/g,'Unknown element “Circel”.','Did you mean Circle?'],
    [/\bunknownElement\b/g,'No valid overload can be generated for this definition.','No analysis world survives to the end of this function.']
  ]){
    for(const match of d.text.matchAll(pattern)){
      if(match[0]==='concat'&&!d.text.includes('$self.radius'))continue;
      result.push({docId:d.id,name:d.name,start:match.index,end:match.index+match[0].length,line:d.text.slice(0,match.index).split('\n').length,message,help});
    }
  }
  return result;
}
function relevantErrors(){
  const ids=new Set([doc().id]);
  if(doc().text.includes('Shapes.'))docs.filter(d=>d.name==='Shapes.vlnc').forEach(d=>ids.add(d.id));
  return docs.filter(d=>ids.has(d.id)).flatMap(d=>diagnostics(d));
}
function paint(line,start,errors){
  const local=errors.filter(e=>e.start>=start&&e.end<=start+line.length).sort((a,b)=>a.start-b.start);
  if(local.length){let result='',offset=0;for(const e of local){result+=paint(line.slice(offset,e.start-start),start+offset,[]);result+='<span class="error-range" data-error-start="'+e.start+'">'+esc(line.slice(e.start-start,e.end-start))+'</span>';offset=e.end-start;}return result+paint(line.slice(offset),start+offset,[]);}
  const pattern=/#[?].*$|\b(?:import|trait|extend|end|object|as|define|if|else|while|return)\b|\b(?:Real|Int|Number|String)\b|\b\d+(?:\.\d+)?\b/g;
  let result='',last=0;
  for(const m of line.matchAll(pattern)){result+=esc(line.slice(last,m.index));const cls=m[0].startsWith('#?')?'comment':/^\d/.test(m[0])?'number':/^(Real|Int|Number|String)$/.test(m[0])?'type':'keyword';result+='<span class="'+cls+'">'+esc(m[0])+'</span>';last=m.index+m[0].length;}
  return result+esc(line.slice(last));
}
function renderSource(){
  if(!doc())return;
  const text=$('source').value,errors=diagnostics(),lineIndex=text.slice(0,$('source').selectionStart).split('\n').length-1;
  let offset=0;
  $('highlight').innerHTML=text.split('\n').map((line,i)=>{const relevant=selectedError&&selectedError.docId===doc().id&&selectedError.line===i+1;const html='<span class="source-line'+(i===lineIndex?' current':'')+(relevant?' relevant':'')+'">'+(paint(line,offset,errors)||' ')+'</span>';offset+=line.length+1;return html;}).join('');
  $('highlight').classList.toggle('error-focus',Boolean(selectedError&&!pinned));syncSource();$('gutter').replaceChildren();
  $('highlight').querySelectorAll('.source-line').forEach((line,i)=>{const n=document.createElement('div');n.className='gutter-line'+(i===lineIndex?' current':'')+(errors.some(e=>e.line===i+1)?' error':'');n.textContent=i+1;n.style.height=line.getBoundingClientRect().height+'px';$('gutter').append(n);});
  syncSource();updateStatus();
}
function syncSource(){const input=$('source');$('highlight').style.width=input.clientWidth+'px';$('highlight').style.transform='translate('+(-input.scrollLeft)+'px,'+(-input.scrollTop)+'px)';$('gutter').style.paddingTop=Math.max(0,12-input.scrollTop)+'px';const first=$('gutter').firstElementChild;if(first)first.style.marginTop=-(Math.max(0,input.scrollTop-12))+'px';}

// Source-mapped execution checkpoints for the Shapes fixture, in bottom-to-top
// order internally. These describe its known operations, not arbitrary programs.
function shapesContext(text,at,functionName){
  if(!text.includes('object Circle as Shape'))return null;
  const lines=text.split('\n');let sourceOffset=0,values=[],last={name:'—',effect:'No preceding operation'};
  const lineNo=text.slice(0,at).split('\n').length-1;
  function operations(line,scope){
    const tokens=[...line.matchAll(/\$self\.radius|\$circle|\\PI|\b(?:Circle|println|perimeter|area)\b|\*\*|[*/=]|\d+(?:\.\d+)?/g)].map(m=>({name:m[0],start:m.index,end:m.index+m[0].length}));
    const named=name=>tokens.find(t=>t.name===name);
    if(scope==='area'&&line.includes('define area')&&line.includes('**')){
      const radius=named('$self.radius'),power=named('**'),number=tokens.find(t=>/^\d/.test(t.name)),pi=named('\\PI'),mul=tokens.find(t=>t.name==='*');
      return [radius,number,power,pi,mul].filter(Boolean);
    }
    if(scope==='perimeter'&&line.includes('define perimeter')){
      const number=tokens.find(t=>/^\d/.test(t.name)),pi=named('\\PI'),radius=named('$self.radius'),muls=tokens.filter(t=>t.name==='*');
      return [number,pi,muls[0],radius,muls[1]].filter(Boolean);
    }
    if(/^\s*\$circle\s*=\s*Circle\s+\d+(?:\.\d+)?\s*$/.test(line))return [tokens.find(t=>/^\d/.test(t.name)),named('Circle'),named('=')];
    if(/^\s*println\s+perimeter\s+\$circle\s*$/.test(line))return [named('$circle'),named('perimeter'),named('println')];
    if(/^\s*\$circle\s*$/.test(line))return [named('$circle')];
    if(/^\s*\$circle\s*\|\s*area\s*\|\s*\d+(?:\.\d+)?\s*\*\s*\|\s*\d+(?:\.\d+)?\s*\/\s*$/.test(line))return tokens;
    return [];
  }
  function apply(op){
    const rules={Circle:{inputs:1,outputs:['Circle'],effect:'Real → Circle'},'=':{inputs:1,outputs:[],effect:'Circle →'},perimeter:{inputs:1,outputs:['Real'],effect:'Circle → Real'},area:{inputs:1,outputs:['Real'],effect:'Circle → Real'},println:{inputs:1,outputs:[],effect:'Real →'}};
    if(/^\d/.test(op.name)){const type=op.name.includes('.')?'Real':'Int';values.push(type);last={name:op.name,effect:'→ '+type};}
    else if(['$circle','$self.radius','\\PI'].includes(op.name)){const type=op.name==='$circle'?'Circle':'Real';values.push(type);last={name:op.name,effect:(op.name.startsWith('$')?'Type: '+type+' · ':'')+'→ '+type};}
    else if(['*','/','**'].includes(op.name)){const inputs=values.splice(Math.max(0,values.length-2),2),type=op.name==='*'&&inputs.every(t=>t==='Int')?'Int':'Real';values.push(type);last={name:op.name,effect:inputs.join(', ')+' → '+type};}
    else if(rules[op.name]){const rule=rules[op.name];values.splice(Math.max(0,values.length-rule.inputs),rule.inputs);values.push(...rule.outputs);last={name:op.name==='='?'$circle =':op.name,effect:rule.effect};}
  }
  for(let i=0;i<=lineNo;i++){
    const line=lines[i],column=at-sourceOffset;
    const isFunctionLine=/\bdefine\s+(area|perimeter)\b/.test(line);
    if(isFunctionLine&&i!==lineNo){sourceOffset+=line.length+1;continue;}
    if(isFunctionLine){values=[];last={name:'—',effect:'Function entry'};}
    const ops=operations(line,isFunctionLine?functionName:null);
    const snapshots=ops.map(op=>{apply(op);return {...op,types:[...values].reverse(),last:{...last}};});
    if(i===lineNo){
      // At a full line's end, the complete statement has finished even if its
      // final source operand was lowered before an earlier source element.
      if(column>=line.trimEnd().length)return {types:[...values].reverse(),last};
      const left=ops.filter(op=>op.end<=column).sort((a,b)=>a.end-b.end).at(-1);
      const right=ops.filter(op=>op.start>=column).sort((a,b)=>a.start-b.start)[0];
      // Between reversed chain operands, inspect the right operand's result:
      // println [Real] perimeter [Circle] $circle. Source order is not evaluation order.
      if(left&&right&&ops.indexOf(right)<ops.indexOf(left)){const point=snapshots.find(s=>s.start===right.start);return {types:point.types,last:point.last};}
      if(left){const point=snapshots.find(s=>s.start===left.start);return {types:point.types,last:point.last};}
      if(snapshots.length){const firstOp=ops[0],before=snapshots.findIndex(s=>s.start===firstOp.start);return before>0?{types:snapshots[before-1].types,last:snapshots[before-1].last}:{types:isFunctionLine?[]:snapshots[0].types.slice(1),last:{name:'—',effect:'Before statement'}};}
      return {types:[...values].reverse(),last};
    }
    sourceOffset+=line.length+1;
  }
  return {types:[...values].reverse(),last};
}
function scaleContext(text,at,type){
  const body=text.indexOf('  * 2'),duplicate=text.indexOf('  dup');
  if(at<body+2)return {types:[type],last:{name:'—',effect:'Function entry'}};
  if(at<body+5)return {types:['Int',type],last:{name:'2',effect:'→ Int'}};
  if(at<duplicate+5)return {types:[type],last:{name:'*',effect:type+', Int → '+type}};
  return {types:[type,type],last:{name:'dup',effect:type+' → '+type+', '+type}};
}
function getContext(){
  const input=$('source'),at=input.selectionStart,text=input.value,lineStart=text.lastIndexOf('\n',at-1)+1,lineEnd=text.indexOf('\n',at),line=text.slice(lineStart,lineEnd<0?text.length:lineEnd),column=at-lineStart;
  const definition=[...text.slice(0,at).matchAll(/\bdefine\s+(\w+)/g)].at(-1);
  let functionName=definition?.[1]||null;if(definition&&/\bend\b/.test(text.slice(definition.index,at)))functionName=null;
  const token=[...line.matchAll(/\\?[\w$]+(?:\.[\w]+)*|\*\*|[+*/-]/g)].find(m=>column>=m.index&&column<m.index+m[0].length);
  const entry=token?catalog.find(e=>e.name===token[0]):null;
  const worldKey=doc().id+':'+functionName,world=functionName==='scale'?(worlds.get(worldKey)||0):0,worldType=['Int','Real','Number'][world];
  let types=[],last={name:'—',effect:'No preceding operation'};
  if(functionName==='scale'){
    const state=scaleContext(text,at,worldType);types=state.types;last=state.last;
  }
  else if(['area','perimeter'].includes(functionName)){
    const tail=line.slice(0,column);
    if(tail.includes('$self.radius')){types=['Real'];last={name:'$self.radius',effect:'Type: Real · → Real'};}
    else if(tail.includes('\\PI')){types=['Real'];last={name:'*',effect:'Int, Real → Real'};}
    else{types=['Int'];last={name:'2',effect:'→ Int'};}
    if(functionName==='area'&&tail.includes('**')){types=['Real'];last={name:'**',effect:'Real, Int → Real'};}
  }else if(line.includes('$circle')){types=['Circle'];last={name:'$circle',effect:'Type: Circle · → Circle'};}
  else if(/\d/.test(line)){types=['Int'];last={name:line.match(/\d+/)[0],effect:'→ Int'};}
  const shapes=shapesContext(text,at,functionName);if(shapes){types=shapes.types;last=shapes.last;}
  return {at,line:text.slice(0,at).split('\n').length,column:column+1,functionName,entry,types,last,worldKey,world,worldType};
}
function updateCursor(force=false){
  const c=getContext();currentContext=c;rememberPosition();
  if(selectedError&&!pinned&&(selectedError.docId!==doc().id||c.at<selectedError.start||c.at>=selectedError.end)){pinned=true;inspectorView='cursor';renderSource();}
  const key=JSON.stringify([doc().id,c.line,c.column,c.world,inspectorView,selectedError?.start,pinned]);if(force||key!==lastContextKey){lastContextKey=key;renderInspection();}updateStatus();
}
function fields(name,type='Real'){return '<h3>Function State</h3><dl><dt>Name</dt><dd>'+esc(['area','perimeter'].includes(name)?'{Circle as Shape}.'+name:name)+'</dd><dt>'+(name==='scale'?'Input stack':'Parameters')+'</dt><dd>'+esc(name==='scale'?type:'$self: Circle')+'</dd><dt>Return</dt><dd>'+esc(name==='scale'?type+', '+type:type)+'</dd></dl>';}
function stack(types){return '<h2>Stack State</h2><div>Top</div><div class="stack">'+(types.length?types.map(t=>'<div>'+esc(t)+'</div>').join(''):'<div class="muted">Empty</div>')+'</div><div>Bottom</div>';}
function renderInspection(){
  const c=currentContext||getContext(),errors=relevantErrors();
  if(!errors.length&&inspectorView==='errors'){inspectorView='cursor';selectedError=null;pinned=false;}
  $('errors-toggle').hidden=!errors.length;$('error-pin').hidden=!selectedError||!pinned;$('expand-pin').textContent=selectedError?selectedError.name+':'+selectedError.line+' · '+selectedError.message:'';
  $('cursor-view').setAttribute('aria-pressed',String(inspectorView==='cursor'));$('errors-toggle').setAttribute('aria-pressed',String(inspectorView==='errors'));
  const useWorlds=c.functionName==='scale'&&!errors.some(e=>e.message.includes('No valid overload'))&&inspectorView==='cursor'&&(!selectedError||pinned);$('world-nav').hidden=!useWorlds;
  if(useWorlds){$('world-pages').innerHTML=['Int','Real','Number'].map((type,i)=>'<button data-world="'+i+'" aria-label="View overload '+(i+1)+' of 3" title="'+type+' → '+type+', '+type+'" aria-pressed="'+(c.world===i)+'">'+(i+1)+'</button>').join('');$('world-pages').querySelectorAll('[data-world]').forEach(b=>b.onclick=()=>{worlds.set(c.worldKey,Number(b.dataset.world));updateCursor(true);});}
  let html='';
  if(selectedError&&!pinned)html='<h2>Error</h2><p>'+esc(selectedError.name)+' · line '+selectedError.line+'</p><p>'+esc(selectedError.message)+'</p><h3>Help</h3><p>'+esc(selectedError.help)+'</p>';
  else if(inspectorView==='errors'){
    html='<h2>Errors</h2>';for(const name of [...new Set(errors.map(e=>e.name))]){html+='<h3>'+esc(name)+'</h3>';errors.filter(e=>e.name===name).forEach(e=>{html+='<button class="error-item" data-error="'+errors.indexOf(e)+'">Line '+e.line+'<br>'+esc(e.message)+'</button>';});}
    html+='<details><summary class="muted">Warnings / lints</summary><p class="muted">No advisory fixture data.</p></details>';
  }else if(errors.some(e=>e.docId===doc().id&&e.line===c.line))html='<h2>State unavailable</h2><p class="muted">This region has a compile error. Open Errors for details.</p>';
  else if(c.entry&&c.entry.signatures.length){
    let chosen=c.entry.signatures[0];if(c.functionName==='scale'){chosen=c.entry.signatures.find(s=>s.startsWith(c.worldType))||chosen;if(c.entry.name==='dup')chosen=c.worldType+' → '+c.worldType+', '+c.worldType;}
    html='<h2>Element '+esc(c.entry.name)+'</h2><p>'+esc(c.entry.doc)+'</p><h3>Resolved Overload</h3><pre>'+esc(chosen)+'</pre>';const other=c.entry.signatures.filter(s=>s!==chosen);if(other.length)html+='<h3>Other Overloads</h3><pre>'+other.map(esc).join('\n')+'</pre>';
  }else if(!doc().text.trim())html=stack([]);
  else html=stack(c.types)+(c.functionName?fields(c.functionName,c.functionName==='scale'?c.worldType:'Real'):'')+'<h3>Last Element</h3><pre>'+esc(c.last.name)+'\n'+esc(c.last.effect)+'</pre>';
  $('inspection').innerHTML=html+'<p class="muted">Simulated analysis</p>';$('inspection').querySelectorAll('[data-error]').forEach(b=>b.onclick=()=>selectError(errors[Number(b.dataset.error)]));$('inspector-error-count').textContent=errors.length;
}
function selectError(error){const index=docs.findIndex(d=>d.id===error.docId);if(index!==active)activate(index);selectedError=error;pinned=false;inspectorView='errors';reveal('state',false);$('source').focus();$('source').setSelectionRange(error.start,error.end);scrollToCaret();currentContext=getContext();renderSource();renderInspection();updateStatus();}
function scrollToCaret(){const c=getContext(),line=$('highlight').children[c.line-1];if(line){$('source').scrollTop=Math.max(0,line.offsetTop-$('source').clientHeight/3);syncSource();}}
function updateStatus(){const d=doc(),c=getContext(),errors=relevantErrors();$('cursor-status').textContent=d.name+' · '+c.line+':'+c.column;$('dirty-status').textContent=d.text===d.saved?'Saved':'Unsaved *';$('error-count').hidden=!errors.length;$('errors-toggle').hidden=!errors.length;$('error-count').textContent=errors.length+' error'+(errors.length===1?'':'s');$('inspector-error-count').textContent=errors.length;$('load-warning').hidden=!errors.length;$('load-errors').textContent=d.name+' · '+errors.length+' compile error'+(errors.length===1?'':'s')+' · view Errors';}
function updateLoaded(){const d=loaded?docs.find(x=>x.id===loaded.id):null,changed=d&&d.revision!==loaded.revision;$('loaded-label').textContent=loaded?loaded.name+' · revision '+loaded.revision+(changed?' · edited since load':''):'No file loaded';$('status-loaded').textContent=loaded?'REPL: '+loaded.name+(changed?' (older revision)':''):'REPL: no loaded file';}
function editSource(text,start,end=start){const d=doc();d.undo.push({text:d.text,caret:$('source').selectionStart,end:$('source').selectionEnd});d.redo=[];d.text=text;d.revision++;$('source').value=text;$('source').setSelectionRange(start,end);sourceChanged();}
function sourceChanged(){clearTimeout(analysisTimer);selectedError=null;pinned=false;inspectorView='cursor';hideCompletion();renderTabs();renderSource();updateCursor(true);updateLoaded();autoCompletion($('source'));}
$('source').addEventListener('beforeinput',()=>{doc().undo.push({text:doc().text,caret:$('source').selectionStart,end:$('source').selectionEnd});doc().redo=[];});
$('source').addEventListener('input',()=>{
  doc().text=$('source').value;doc().revision++;
  const c=getContext(),lines=doc().text.split('\n');
  if(/^\s*end$/.test(lines[c.line-1])){
    const lineStart=doc().text.lastIndexOf('\n',c.at-1)+1,old=lines[c.line-1],prefix=doc().text.slice(0,lineStart),openings=[...prefix.matchAll(/^(\s*)(?:trait|object|define|if|while)\b/gm)];
    const indent=doc().text.includes('object Circle as Shape')&&c.line<=12?'':openings.at(-1)?.[1]||'';
    if(old!==indent+'end'){const delta=old.length-(indent.length+3);lines[c.line-1]=indent+'end';doc().text=lines.join('\n');$('source').value=doc().text;$('source').setSelectionRange(c.at-delta,c.at-delta);}
  }
  sourceChanged();
});
$('source').addEventListener('scroll',syncSource);
for(const event of ['click','keyup','select'])$('source').addEventListener(event,()=>{cancelAnimationFrame(renderFrame);renderFrame=requestAnimationFrame(()=>{updateCursor();renderSource();});});
$('source').addEventListener('click',event=>{
  const hit=[...$('highlight').querySelectorAll('.error-range')].find(span=>[...span.getClientRects()].some(rect=>event.clientX>=rect.left&&event.clientX<=rect.right&&event.clientY>=rect.top&&event.clientY<=rect.bottom));
  const at=hit?Number(hit.dataset.errorStart):$('source').selectionStart,error=diagnostics().find(e=>at>=e.start&&at<e.end);
  if(error)selectError(error);
});
function undoEdit(redo=false){const from=redo?doc().redo:doc().undo,to=redo?doc().undo:doc().redo;if(!from.length)return;to.push({text:doc().text,caret:$('source').selectionStart,end:$('source').selectionEnd});const value=from.pop();doc().text=value.text;doc().revision++;$('source').value=value.text;$('source').setSelectionRange(value.caret,value.end);sourceChanged();}
function indentSelection(direction){
  const input=document.activeElement===$('command')?$('command'):$('source'),width=Number($('indent-width').value)||2,caret=input.selectionStart,end=input.selectionEnd;
  const first=input.value.lastIndexOf('\n',caret-1)+1,last=input.value.indexOf('\n',end),stop=last<0?input.value.length:last;
  const lines=input.value.slice(first,stop).split('\n');let firstDelta=0;
  const replacement=lines.map((line,i)=>{const value=direction>0?' '.repeat(width)+line:line.replace(new RegExp('^ {1,'+width+'}'),'');if(i===0)firstDelta=value.length-line.length;return value;}).join('\n');
  const text=input.value.slice(0,first)+replacement+input.value.slice(stop),delta=replacement.length-(stop-first);
  if(input===$('source'))editSource(text,Math.max(first,caret+firstDelta),Math.max(first,end+delta));else{input.value=text;input.setSelectionRange(Math.max(first,caret+firstDelta),Math.max(first,end+delta));resizeCommand();}
}
function insertText(input,value){const start=input.selectionStart,end=input.selectionEnd;if(input===$('source'))editSource(input.value.slice(0,start)+value+input.value.slice(end),start+value.length);else{input.setRangeText(value,start,end,'end');input.dispatchEvent(new Event('input'));}}
function newline(input){const start=input.selectionStart,line=input.value.slice(input.value.lastIndexOf('\n',start-1)+1,start);let indent=line.match(/^ */)[0];if(/\b(?:define|object|trait|if|while)\b.*=>\s*$/.test(line))indent+=' '.repeat(Number($('indent-width').value)||2);insertText(input,'\n'+indent);}

function showCompletion(input,explicit=false){
  const prefix=input.value.slice(0,input.selectionStart).match(/[\w*+/$.-]*$/)[0];
  const types=input===$('source')?(currentContext?.types||[]):demoStack.map(value=>typeof value==='object'?'Circle':Number.isInteger(value)?'Int':'Real'),hint=types[0];
  const items=catalog.filter(item=>item.name.startsWith(prefix)&&(!item.type||!hint||item.type===hint||(item.type==='Number'&&['Int','Real','Number'].includes(hint))));
  if(!items.length||(!explicit&&(items.length>8||!prefix))){hideCompletion();return;}
  completion={input,prefix,items,index:0,explicit};drawCompletion();
}
function autoCompletion(input){showCompletion(input,false);}
function drawCompletion(){
  const panel=completion.input===$('source')?$('completion'):$('repl-completion');panel.hidden=false;
  panel.innerHTML=completion.items.map((item,i)=>'<button class="completion-item" role="option" aria-selected="'+(i===completion.index)+'" data-choice="'+i+'">'+esc(item.name)+'<small>'+esc(item.signatures[0]||item.doc)+(item.signatures.length>1?' · '+item.signatures.length+' overloads':'')+'</small></button>').join('');
  panel.querySelectorAll('[data-choice]').forEach(b=>{b.onmousedown=e=>e.preventDefault();b.onclick=()=>{completion.index=Number(b.dataset.choice);acceptCompletion();};});panel.querySelector('[aria-selected=true]')?.scrollIntoView({block:'nearest'});
}
function hideCompletion(){completion=null;$('completion').hidden=true;$('repl-completion').hidden=true;}
function acceptCompletion(){const c=completion,name=c.items[c.index].name,start=c.input.selectionStart-c.prefix.length,end=c.input.selectionStart;hideCompletion();if(c.input===$('source'))editSource(c.input.value.slice(0,start)+name+c.input.value.slice(end),start+name.length);else{c.input.setRangeText(name,start,end,'end');resizeCommand();}hideCompletion();c.input.focus();}
function completionKey(e,input){
  if(e.ctrlKey&&e.code==='Space'){e.preventDefault();showCompletion(input,true);return true;}
  if(!completion||completion.input!==input)return false;
  if(['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();completion.index=(completion.index+(e.key==='ArrowUp'?-1:1)+completion.items.length)%completion.items.length;drawCompletion();return true;}
  if(e.key==='Tab'){e.preventDefault();acceptCompletion();return true;}
  if(e.key==='Escape'){e.preventDefault();hideCompletion();return true;}
  if(e.key==='Enter')hideCompletion();return false;
}
$('source').addEventListener('keydown',e=>{
  if(completionKey(e,$('source')))return;
  if(e.key==='Tab'){e.preventDefault();insertText($('source'),' '.repeat(Number($('indent-width').value)||2));}
  if(e.key==='Enter'){e.preventDefault();newline($('source'));}
  if(e.ctrlKey&&e.key.toLowerCase()==='z'){e.preventDefault();undoEdit(e.shiftKey);}
  if(e.ctrlKey&&e.key.toLowerCase()==='y'){e.preventDefault();undoEdit(true);}
});
function setLock(){locked=!locked;$('enter-lock').textContent=locked?'Enter: newline [locked] · Ctrl/Shift+Enter toggles':'Enter: submit · Ctrl/Shift+Enter toggles';$('enter-lock').setAttribute('aria-pressed',String(locked));}
$('enter-lock').onclick=setLock;$('menu-lock').onclick=()=>{setLock();$('file-menu').open=false;};
function resizeCommand(){$('command').style.height='24px';$('command').style.height=Math.min(120,$('command').scrollHeight)+'px';}
$('command').oninput=()=>{resizeCommand();autoCompletion($('command'));};
$('command').onkeydown=e=>{
  if((e.ctrlKey||e.shiftKey)&&!e.altKey&&e.key==='Enter'){e.preventDefault();hideCompletion();setLock();return;}
  if(completionKey(e,$('command')))return;
  if(e.key==='Tab'){e.preventDefault();if(locked)insertText($('command'),' '.repeat(Number($('indent-width').value)||2));else showCompletion($('command'),true);return;}
  if(e.key==='Enter'){e.preventDefault();if(locked)newline($('command'));else submit();return;}
  if(!locked&&['ArrowUp','ArrowDown'].includes(e.key)){
    const input=$('command'),first=!input.value.slice(0,input.selectionStart).includes('\n'),last=!input.value.slice(input.selectionEnd).includes('\n');
    if((e.key==='ArrowUp'&&first)||(e.key==='ArrowDown'&&last)){e.preventDefault();if(historyIndex===history.length)savedDraft=input.value;historyIndex=Math.max(0,Math.min(history.length,historyIndex+(e.key==='ArrowUp'?-1:1)));input.value=historyIndex===history.length?savedDraft:history[historyIndex];resizeCommand();}
  }
};
function appendTranscript(text,kind='output'){
  transcript.push({text,kind});while(transcript.reduce((sum,item)=>sum+item.text.length,0)>24000&&transcript.length>1)dropped+=transcript.shift().text.length;renderTranscript();
}
function renderTranscript(){
  const log=$('transcript'),atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<40;log.replaceChildren();
  if(dropped){const marker=document.createElement('div');marker.className='transcript-entry muted';marker.textContent='[Older output dropped · '+dropped+' characters]';log.append(marker);}
  transcript.forEach(item=>{const entry=document.createElement('div');entry.className='transcript-entry '+item.kind;entry.textContent=item.text;log.append(entry);});if(atBottom)log.scrollTop=log.scrollHeight;
}
function displayValue(value){return value&&typeof value==='object'?'Circle{radius: '+value.radius+'}':String(value);}
function submit(){
  if(running){notice('Stop current execution before submitting.');return;}
  const text=$('command').value;if(!text.trim())return;
  history.push(text);historyIndex=history.length;savedDraft='';try{localStorage.setItem('valiance-design-history',JSON.stringify(history.slice(-200)));}catch{}
  $('command').value='';resizeCommand();appendTranscript('> '+text,'input');
  const circle=text.trim().match(/^Circle\s+(-?\d+(?:\.\d+)?)$/);
  if(circle)demoStack.unshift({radius:Number(circle[1])});
  else if(text.trim()==='$.radius'&&demoStack[0]?.radius!==undefined)demoStack[0]=demoStack[0].radius;
  else if(/^-?\d+(?:\.\d+)?$/.test(text.trim()))demoStack.unshift(Number(text.trim()));
  else if(text.trim()==='drop')demoStack.shift();
  else if(text.trim()==='dup'&&demoStack.length)demoStack.unshift(demoStack[0]);
  else if(text.trim()===':reset'){reset();return;}
  else if(text.trim()===':clear'){clearTranscript();return;}
  else if(/^(?:[*/+]\s*-?\d+(?:\.\d+)?|dup)(?:\s*\|\s*(?:[*/+]\s*-?\d+(?:\.\d+)?|dup))*$/.test(text.trim())&&typeof demoStack[0]==='number'){
    for(const part of text.split('|').map(p=>p.trim())){if(part==='dup'){demoStack.unshift(demoStack[0]);continue;}const amount=Number(part.slice(1));if(part[0]==='*')demoStack[0]*=amount;else if(part[0]==='/')demoStack[0]/=amount;else demoStack[0]+=amount;}
  }else{appendTranscript('[No execution fixture for this command. Input is retained in history.]','muted');return;}
  appendTranscript(demoStack.length?demoStack.map(displayValue).join(' | '):'Stack is empty','result');
}
function load(){
  if(running){notice('Stop current execution before loading again.');return;}
  const errors=relevantErrors();if(errors.length){
    inspectorView='errors';selectedError=null;pinned=false;reveal('state',false);terminal.classList.remove('hide-repl');terminal.dataset.compactView='editor';
    appendTranscript('Alt+X blocked · '+doc().name+' · '+errors.length+' compile error'+(errors.length===1?'':'s')+'\n'+errors.map(e=>e.name+':'+e.line+' · '+e.message).join('\n'),'compile-error');
    $('transcript').scrollTop=$('transcript').scrollHeight;renderInspection();$('source').focus();notice('Load blocked · see Errors and REPL');return;
  }
  loaded={id:doc().id,name:doc().name,revision:doc().revision,text:doc().text};demoStack=[];appendTranscript('Fresh session · '+loaded.name+' · revision '+loaded.revision,'session-separator');
  if(loaded.text.includes('Circle')){const radius=Number(loaded.text.match(/\$circle\s*=\s*Circle\s+(\d+(?:\.\d+)?)/)?.[1]||10);demoStack=[{radius}];appendTranscript('Illustrative output: '+(2*Math.PI*radius).toFixed(6));}
  else appendTranscript('[Program loaded in the UI simulation; no real compilation.]','muted');
  reveal('repl');$('command').focus();updateLoaded();notice('Loaded unsaved buffer · simulated execution');
}
function clearTranscript(){transcript=[];dropped=0;renderTranscript();}
function reset(){if(running)finishRun('stopped');clearTranscript();demoStack=[];loaded=null;updateLoaded();}
function interrupted(reason){demoStack=[];loaded=null;updateLoaded();appendTranscript(reason+' · runtime state reset','runtime-error');}
function finishRun(reason='finished'){clearInterval(runTimer);running=false;$('stop').hidden=true;$('load').disabled=false;if(reason==='stopped')interrupted('Execution stopped');else notice('Demo run finished');}
function longRun(){if(running)return;loaded={id:doc().id,name:doc().name,revision:doc().revision,text:doc().text};running=true;$('stop').hidden=false;$('load').disabled=true;appendTranscript('Fresh session · long-running design fixture','session-separator');reveal('repl');updateLoaded();let count=0;runTimer=setInterval(()=>{appendTranscript('Tick '+(++count)+' · running original revision '+loaded.revision);if(count>=100)finishRun();},400);}
$('load').onclick=load;$('stop').onclick=()=>finishRun('stopped');$('long-run').onclick=longRun;$('fault').onclick=()=>{if(running)finishRun();reveal('repl');interrupted('Runtime fault (design fixture)');};$('clear').onclick=clearTranscript;$('reset').onclick=reset;
function download(name,text){const url=URL.createObjectURL(new Blob([text],{type:'text/plain'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export').onclick=()=>download('valiance-transcript.txt',(dropped?'[Older output dropped · '+dropped+' characters]\n\n':'')+transcript.map(item=>item.text).join('\n\n'));

function reveal(pane,switchCompact=true){terminal.classList.remove('repl-only','hide-'+pane);if(switchCompact)terminal.dataset.compactView=pane;}
$('toggle-repl').onclick=()=>{terminal.classList.remove('repl-only');terminal.classList.toggle('hide-repl');terminal.dataset.compactView='editor';$('file-menu').open=false;};
$('toggle-state').onclick=()=>{terminal.classList.remove('repl-only');terminal.classList.toggle('hide-state');terminal.dataset.compactView='editor';$('file-menu').open=false;};
$('only-repl').onclick=()=>{terminal.classList.toggle('repl-only');$('file-menu').open=false;};
$('wrap').onclick=()=>{terminal.classList.toggle('no-wrap');const off=terminal.classList.contains('no-wrap');$('source').wrap=off?'off':'soft';$('wrap').textContent=off?'Wrap: off':'Wrap: on';$('file-menu').open=false;renderSource();};
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>reveal(b.dataset.view);
$('cursor-view').onclick=()=>{inspectorView='cursor';if(selectedError)pinned=true;renderInspection();};
function openErrors(){if(!relevantErrors().length)return;inspectorView=inspectorView==='errors'?'cursor':'errors';reveal('state');renderInspection();}
$('errors-toggle').onclick=openErrors;$('error-count').onclick=openErrors;
$('load-errors').onclick=()=>{inspectorView='errors';reveal('state');renderInspection();};
$('expand-pin').onclick=()=>{if(selectedError)selectError(selectedError);};$('dismiss-pin').onclick=()=>{selectedError=null;pinned=false;renderInspection();renderSource();};
function worldStep(delta){const c=currentContext;if(c?.functionName==='scale'){worlds.set(c.worldKey,(c.world+delta+3)%3);updateCursor(true);}}
$('previous-world').onclick=()=>worldStep(-1);$('next-world').onclick=()=>worldStep(1);
function resizeDivider(id,axis){
  const divider=$(id);divider.onpointerdown=e=>{divider.setPointerCapture(e.pointerId);};
  divider.onpointermove=e=>{if(!divider.hasPointerCapture(e.pointerId))return;const bounds=terminal.getBoundingClientRect();if(axis==='x')terminal.style.setProperty('--state-width',Math.max(230,Math.min(bounds.width-260,bounds.right-e.clientX))+'px');else terminal.style.setProperty('--repl-height',Math.max(130,Math.min(bounds.height-150,bounds.bottom-e.clientY-30))+'px');};
  divider.onpointerup=e=>divider.releasePointerCapture(e.pointerId);
  divider.onkeydown=e=>{const keys=axis==='x'?['ArrowLeft','ArrowRight']:['ArrowUp','ArrowDown'];if(!keys.includes(e.key))return;e.preventDefault();const target=axis==='x'?$('state'):$('repl'),size=axis==='x'?target.offsetWidth:target.offsetHeight;terminal.style.setProperty(axis==='x'?'--state-width':'--repl-height',Math.max(axis==='x'?230:130,size+(e.key===keys[0]?16:-16))+'px');};
}
resizeDivider('vertical-divider','x');resizeDivider('horizontal-divider','y');
new ResizeObserver(()=>{terminal.classList.toggle('compact',terminal.clientWidth<820);renderSource();}).observe(terminal);
new ResizeObserver(()=>{syncSource();requestAnimationFrame(renderSource);}).observe($('code-surface'));
function search(openReplace=false){$('search-bar').hidden=false;for(const id of ['replace','replace-one','replace-all'])$(id).hidden=!openReplace;$('search').focus();$('search').select();}
function findNext(){const query=$('search').value;if(!query)return;const input=$('source'),start=input.value.indexOf(query,input.selectionEnd),at=start<0?input.value.indexOf(query):start;if(at>=0){input.focus();input.setSelectionRange(at,at+query.length);scrollToCaret();updateCursor(true);}const count=input.value.split(query).length-1;$('search-count').textContent=count+' match'+(count===1?'':'es');}
$('find-next').onclick=findNext;$('search').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();findNext();}};$('close-search').onclick=()=>{$('search-bar').hidden=true;$('source').focus();};
$('replace-one').onclick=()=>{const input=$('source'),query=$('search').value;if(!query)return;if(input.value.slice(input.selectionStart,input.selectionEnd)!==query){findNext();return;}const start=input.selectionStart;editSource(input.value.slice(0,start)+$('replace').value+input.value.slice(input.selectionEnd),start+$('replace').value.length);findNext();};
$('replace-all').onclick=()=>{const query=$('search').value;if(query)editSource($('source').value.split(query).join($('replace').value),0);};
function dialog(title,message,buttons,name=null){return new Promise(resolve=>{const box=$('file-dialog');$('dialog-title').textContent=title;$('dialog-message').textContent=message;$('dialog-name').hidden=name===null;$('dialog-name').value=name||'';$('dialog-buttons').replaceChildren();buttons.forEach(label=>{const b=document.createElement('button');b.value=label;b.textContent=label;$('dialog-buttons').append(b);});box.onclose=()=>resolve({action:box.returnValue,name:$('dialog-name').value});box.showModal();});}
async function save(as=false){if(as||!doc().path){const result=await dialog('Save As','Browser prototype saves a downloaded copy.',['Cancel','Save'],doc().name==='Untitled'?'Untitled.vlnc':doc().name);if(result.action!=='Save'||!result.name.trim())return false;doc().name=result.name.trim();doc().path=doc().name;}download(doc().name,doc().text);doc().saved=doc().text;renderTabs();updateStatus();return true;}
async function canClose(){if(doc().text===doc().saved)return true;const result=await dialog('Unsaved changes',doc().name+' has unsaved changes.',['Cancel','Discard','Save']);if(result.action==='Save')return save();return result.action==='Discard';}
async function closeDoc(index=active){
  const previous=doc().id;activate(index);if(!await canClose()){activate(docs.findIndex(d=>d.id===previous));return;}
  docs.splice(index,1);if(!docs.length)docs.push(makeDoc('Untitled'));
  const existing=docs.findIndex(d=>d.id===previous);active=existing>=0?existing:Math.min(index,docs.length-1);activate(active,false);
}
async function renameDoc(index=active){
  const d=docs[index],result=await dialog('Rename file','Prototype changes the document name; it cannot rename a file on disk.',['Cancel','Rename'],d.name);
  if(result.action!=='Rename')return;const name=result.name.trim();
  if(!name||/[\\/:*?"<>|]/.test(name)||name==='.'||name==='..'){notice('Choose a valid file name');return;}
  const path=d.path?d.path.slice(0,Math.max(d.path.lastIndexOf('/'),d.path.lastIndexOf('\\'))+1)+name:null;
  if(path&&docs.some(other=>other.id!==d.id&&other.path===path)){notice('That file is already open');return;}
  d.name=name;d.path=path;selectedError=null;pinned=false;renderTabs();renderSource();updateCursor(true);updateLoaded();
}
async function fileAction(action){
  $('file-menu').open=false;
  if(action==='new')addDoc('Untitled'+(++untitledCount>1?' '+untitledCount:''));
  if(action==='rename')await renameDoc();if(action==='save')await save();if(action==='save-as')await save(true);if(action==='close')await closeDoc();if(action==='open')$('open-file').click();
  if(action==='recent'){const result=await dialog('Open Recent','Choose a provided example.',['Cancel',...Object.keys(fixtures)]);if(fixtures[result.action]!==undefined){const existing=docs.findIndex(d=>d.path===result.action);if(existing>=0)activate(existing);else addDoc(result.action,fixtures[result.action],result.action);}}
  if(action==='quit'){for(let i=0;i<docs.length;i++){activate(i);if(!await canClose())return;}notice('Quit confirmed · prototype stays open for review');}
}
for(const b of document.querySelectorAll('[data-file]'))b.onclick=()=>fileAction(b.dataset.file);
$('new-tab').onclick=()=>fileAction('new');$('open-file').onchange=async e=>{
  const file=e.target.files[0];if(!file)return;
  const key=[file.name,file.lastModified,file.size,file.webkitRelativePath].join(':');
  const existing=docs.findIndex(d=>d.openedFileKey===key);
  if(existing>=0)activate(existing);else{const text=await file.text();addDoc(file.name,text);doc().openedFileKey=key;}
  e.target.value='';
};
$('lab-toggle').onclick=()=>{$('lab').hidden=!$('lab').hidden;$('lab-toggle').setAttribute('aria-expanded',String(!$('lab').hidden));};
const references=['Hero Example','Inside a function','Inside another function','Cursor on a specific element','REPL only','No REPL','Errors present','Clicked on an error','Just the editor'];
for(const name of references){const a=document.createElement('a');a.textContent=name;a.href='../assets/terminal-editor/'+encodeURIComponent(name+'.PNG');a.target='_blank';a.rel='noopener';$('screenshots').append(a);}
for(const role of roles){const option=document.createElement('option');option.value=role;option.textContent=role;$('style-role').append(option);}
function syncColor(){$('style-color').value=getComputedStyle(document.documentElement).getPropertyValue('--'+$('style-role').value).trim();}
$('style-role').onchange=syncColor;$('style-color').oninput=e=>document.documentElement.style.setProperty('--'+$('style-role').value,e.target.value);
const themePresets={
  dark:{},
  contrast:{background:'#000000',text:'#ffffff',divider:'#eeeeee',comment:'#bbbbbb',gutter:'#111111','state-background':'#000000','repl-background':'#000000'},
  ocean:{background:'#101c2c',text:'#e2edf9',gutter:'#16273b',tabs:'#21364c','active-tab':'#15263b','inactive-tab':'#314963',divider:'#56728e',keyword:'#d596f0',type:'#65cde8',number:'#f8bc84',comment:'#93a7bf',status:'#1a2e44',focus:'#80c9ff',dim:'#72859b','state-background':'#101c2c','repl-background':'#101c2c'},
  plum:{background:'#231a2a',text:'#f4e9f5',gutter:'#2b2035',tabs:'#40304b','active-tab':'#2b2035','inactive-tab':'#594464',divider:'#8a7198',keyword:'#ed96ce',type:'#a6c5ff',number:'#ffc497',comment:'#b09caf',status:'#34263f',focus:'#d4b6ff',dim:'#8e7d94','state-background':'#231a2a','repl-background':'#231a2a'},
  amber:{background:'#211c15',text:'#f3e8d2',gutter:'#2d251b',tabs:'#433828','active-tab':'#2d251b','inactive-tab':'#5c4c35',divider:'#99856a',keyword:'#e7a7d8',type:'#89cbd8',number:'#ffca72',comment:'#b1a18a',status:'#352b20',focus:'#ffd393',dim:'#978775','state-background':'#211c15','repl-background':'#211c15'},
  paper:{background:'#f5f2eb',text:'#282a35',gutter:'#e9e4dc',tabs:'#d8d1c7','active-tab':'#c3bbaf','inactive-tab':'#e5dfd5',divider:'#827a70',keyword:'#8c287e',type:'#006e8c',number:'#9a4919',comment:'#68635b',error:'#b02736',output:'#237b39',status:'#e8e1d6',focus:'#285f9e',dim:'#817c75','state-background':'#f5f2eb','repl-background':'#f5f2eb'}
};
function theme(){const name=$('theme').value;for(const [key,value] of Object.entries({...defaults,...themePresets[name]}))document.documentElement.style.setProperty('--'+key,value);document.documentElement.style.colorScheme=name==='paper'?'light':'dark';syncColor();}
$('theme').onchange=theme;$('restore-style').onclick=()=>{$('theme').value='dark';theme();};syncColor();
$('slow').onclick=()=>{clearTimeout(analysisTimer);$('inspection').replaceChildren();analysisTimer=setTimeout(()=>{$('inspection').textContent='Updating…';analysisTimer=setTimeout(()=>updateCursor(true),900);},501);};
function setScenario(value){
  if(running)finishRun('stopped');selectedError=null;pinned=false;inspectorView='cursor';terminal.classList.remove('hide-state','hide-repl','repl-only');terminal.dataset.compactView='editor';
  docs=Object.entries(fixtures).map(([name,text])=>{const d=makeDoc(name,text,name);if(name==='Overloads.vlnc')d.caret=d.end=text.indexOf('* 2')+3;return d;});active=1;
  if(value==='worlds')active=docs.findIndex(d=>d.name==='Overloads.vlnc');
  if(value==='blank'){docs=[makeDoc('Untitled')];active=0;reset();}
  if(['errors','selected-error'].includes(value)){doc().text=shapeSource.replace('\\PI * $self.radius','\\PI concat $self.radius').replace('= Circle 10','= Circel 10');doc().saved=doc().text;}
  $('source').value=doc().text;
  const target=value==='perimeter'?doc().text.indexOf(' * $self'):value==='area'?doc().text.indexOf(' **'):value==='element'?doc().text.indexOf('**'):value==='worlds'?doc().text.indexOf('* 2')+3:doc().text.indexOf('$circle |');
  doc().caret=doc().end=Math.max(0,target);$('source').setSelectionRange(doc().caret,doc().end);
  terminal.classList.toggle('hide-repl',['no-repl','editor-only'].includes(value));terminal.classList.toggle('hide-state',value==='editor-only');terminal.classList.toggle('repl-only',value==='repl-only');
  if(value==='repl-only')terminal.dataset.compactView='repl';if(value==='errors')inspectorView='errors';renderTabs();renderSource();updateCursor(true);updateLoaded();
  if(value==='selected-error')selectError(diagnostics()[0]);$('source').focus();if(value==='repl-only')$('command').focus();
}
$('scenario').onchange=e=>setScenario(e.target.value);$('restore-example').onclick=()=>setScenario($('scenario').value);
document.addEventListener('keydown',e=>{
  if(e.defaultPrevented)return;
  const sourceFocus=e.target===$('source'),commandFocus=e.target===$('command');
  if(e.altKey&&e.key.toLowerCase()==='x'){e.preventDefault();load();}
  if(e.ctrlKey&&['[',']'].includes(e.key)&&(sourceFocus||commandFocus)){e.preventDefault();indentSelection(e.key===']'?1:-1);}
  if(e.ctrlKey&&e.key.toLowerCase()==='f'){e.preventDefault();search();}
  if(e.ctrlKey&&e.key.toLowerCase()==='h'){e.preventDefault();search(true);}
  if(e.ctrlKey&&e.key.toLowerCase()==='s'){e.preventDefault();fileAction(e.shiftKey?'save-as':'save');}
  if(e.ctrlKey&&e.key.toLowerCase()==='o'){e.preventDefault();fileAction('open');}
  if(e.ctrlKey&&e.key.toLowerCase()==='n'){e.preventDefault();fileAction('new');}
  if(e.ctrlKey&&e.key.toLowerCase()==='w'){e.preventDefault();fileAction('close');}
  if(e.ctrlKey&&e.key.toLowerCase()==='q'){e.preventDefault();fileAction('quit');}
  if(e.ctrlKey&&['PageUp','PageDown'].includes(e.key)){e.preventDefault();activate((active+(e.key==='PageUp'?-1:1)+docs.length)%docs.length);$('source').focus();}
  if(e.ctrlKey&&e.key.toLowerCase()==='c'&&running){e.preventDefault();finishRun('stopped');}
  if(e.key==='F6'){e.preventDefault();hideCompletion();const targets=[$('source'),$('state'),$('command')];let current=targets.indexOf(document.activeElement),next=(current+1)%3;if(terminal.classList.contains('compact'))reveal(['editor','state','repl'][next]);else for(let i=0;i<3&&!targets[next].getClientRects().length;i++)next=(next+1)%3;targets[next].focus();}
  if(e.key==='Escape'&&!completion&&selectedError){selectedError=null;pinned=false;renderInspection();renderSource();}
});
setScenario('hero');
appendTranscript('> Circle 20','input');appendTranscript('  Circle{radius: 20}','result');
appendTranscript('> $.radius','input');appendTranscript('  20','result');
appendTranscript('> * 2 | dup | * 3','input');appendTranscript('  120 | 40','result');
