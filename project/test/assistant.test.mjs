import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

class El {
  constructor(id='') { this.id=id; this.children=[]; this.value=''; this.textContent=''; this.dataset={}; this.classList={add(){},remove(){}}; }
  appendChild(x){ this.children.push(x); return x; }
  addEventListener(){}
  setAttribute(){}
  querySelectorAll(){ return []; }
  querySelector(){ return null; }
  scrollTop=0;
}
const els = new Map();
for (const id of ['aiAssistant','aiAssistantInput','aiAssistantMessages','aiAssistantQuick','aiAssistantSend','catalogAssistantBtn']) els.set(id,new El(id));
els.get('aiAssistant').children=[];
const items=[];
const entries=[
 {id:'sofa-2',base:'sofa',family:'seating',name:'Canapé Tipaza 2 places',w:1.6,d:.9,price:67500,color:'#9B8B7A',colors:['#9B8B7A'],variants:[],styles:['moderne'],tags:['seating'],materials:[],assets:{}},
 {id:'coffee',base:'table',family:'tables',name:'Table basse Annaba',w:1.1,d:.6,price:18900,color:'#8C6A4F',colors:['#8C6A4F'],variants:[],styles:['moderne'],tags:['tables'],materials:[],assets:{}},
 {id:'lamp',base:'lamp',family:'decor',name:'Lampadaire Sétif',w:.35,d:.35,price:9900,color:'#E8B33A',colors:['#E8B33A'],variants:[],styles:['moderne'],tags:['decor'],materials:[],assets:{}},
 {id:'armchair',base:'armchair',family:'seating',name:'Fauteuil Oran',w:.95,d:.9,price:34500,color:'#3A4049',colors:['#3A4049'],variants:[],styles:['scandinave'],tags:['seating'],materials:[],assets:{}},
];
let uid=0;
const App={state:{items},getSelected(){return items[items.length-1]||null},roomFit(){return {x:0,y:0,w:1600,h:1200}},getCanvasSize(){return {width:1600,height:1200}}};
const AppActions={
 addItemsBatch(ids, opts, meta={}){const created=[];ids.forEach((id,i)=>{const e=entries.find(x=>x.id===id);const o=typeof opts==='function'?opts(e,i,created):(opts||{});const item={uid:`u${++uid}`,entryId:e.id,catId:e.base,name:e.name,price:e.price,color:o.color||e.color,w:e.w,d:e.d,x:o.x||800,y:o.y||800,rot:o.rot||0,scale:o.scale||1,z:items.length+1};items.push(item);created.push(item)});return created},
 updateItem(uid,patch){const item=items.find(x=>x.uid===uid);Object.assign(item,patch);return {item,before:{}}}
};
const context={console, setTimeout, document:{getElementById:id=>els.get(id),createElement:()=>new El()}, window:null};
context.window=context; context.App=App; context.AppActions=AppActions; context.CATALOG=entries; context.catalogEntry=id=>entries.find(e=>e.id===id); context.formatPrice=n=>`${n} DA`; context.spriteFor=()=>({src:'x'});
vm.runInNewContext(fs.readFileSync('js/ai-assistant.js','utf8'),context,{filename:'ai-assistant.js'});
const A=context.CigogneAssistant;
assert.deepEqual(A.parseAddCommand('Ajoute deux lampes de chaque côté du canapé')[0].base,'lamp');
assert.equal(A.parseAddCommand('Ajoute deux lampes de chaque côté du canapé')[0].count,2);
assert.deepEqual(A.parseAddCommand('Ajoute une table basse devant le canapé')[0].base,'table');
const multi=A.parseAddCommand('Ajoute un canapé et une table basse devant le canapé');
assert.equal(Array.from(multi, x => x.base).join(','), 'sofa,table');
A.execute('Ajoute deux lampes de chaque côté du canapé');
assert.equal(items.length,2);
assert.equal(items.filter(x=>x.entryId==='lamp').length,2);
A.execute('Ajoute un canapé et une table basse devant le canapé');
assert.equal(items.filter(x=>x.entryId==='sofa-2').length,1);
assert.equal(items.filter(x=>x.entryId==='coffee').length,1);
assert.equal(A.parseAddCommand('Mets 3 plantes dans le coin')[0].count,3);
assert.equal(A.parseAddCommand('Ajoute 4 chaises autour de la table')[0].base,'chair');
assert.equal(A.parseAddCommand('Ajoute 4 chaises autour de la table')[0].count,4);
assert.equal(A.parseAddCommand('Ajoute une lampe à gauche du canapé')[0].base,'lamp');
console.log('assistant tests: 10/10 passed');
