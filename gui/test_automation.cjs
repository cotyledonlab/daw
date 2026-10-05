const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('./editor.js');
const Automation = require('./automation.js');
const History = require('./history.js');
class Node {
  constructor(tag, doc) { this.tag=tag; this.ownerDocument=doc; this.children=[]; this.listeners={}; this.attrs={}; this.value=''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  setAttribute(key,value) { this.attrs[key]=String(value); }
  addEventListener(type,fn) { this.listeners[type]=fn; }
  all(tag) { return this.children.flatMap(n=>[...(n.tag===tag?[n]:[]),...n.all(tag)]); }
  fire(type,event={}) { return this.listeners[type]?.({preventDefault(){},...event}); }
  focus() { this.ownerDocument.activeElement=this; }
}
const base = () => E.addEffect(E.editMixer(E.createDemoSession(),0,{gain:0.7,pan:-0.2}),0,{kind:'gain',gain:0.4,bypass:false},'volume');
function setup() {
  const doc={createElement:tag=>new Node(tag,doc)}, container=doc.createElement('section'), errors=[],history=new History();
  let current=base(), fail=false, edits=0;
  const view=Automation.create(container,{editor:E,trackIndex:0,effectId:'volume',onError:e=>errors.push(e),onEdit:async next=> {
    edits++; if(fail) return false; history.commit(current,next);current=next;view.render(current); return true;
  }});
  view.render(current);
  return {container,errors,history,view,get current(){return current;},get edits(){return edits;},set fail(v){fail=v;}};
}
const input=(s,label)=>s.container.all('input').find(n=>n.attrs['aria-label']===`volume ${label}`);
const click=(s,label)=>s.container.all('button').find(n=>n.attrs['aria-label']===`volume ${label}`).fire('click');
test('gain lane mutations preserve precision, devices, mixer and independent saved snapshots',()=> {
  const source=base(),before=structuredClone(source);
  let next=E.addGainAutomationPoint(source,0,'volume',{frame:1234567,value:0.123456789});
  next=E.addGainAutomationPoint(next,0,'volume',{frame:23,value:4});
  assert.deepEqual(next.tracks[0].automation[0],{effect_id:'volume',parameter:'gain',interpolation:'step',points:[{frame:23,value:4},{frame:1234567,value:0.123456789}]});
  next=E.editGainAutomationPoint(next,0,'volume',23,{frame:24,value:0});
  assert.equal(next.schema_version,10);assert.deepEqual(next.tracks[0].mixer,source.tracks[0].mixer);assert.deepEqual(next.tracks[0].clips,source.tracks[0].clips);
  assert.deepEqual(source,before);assert.equal(E.validate(JSON.parse(JSON.stringify(next))),null);
  next=E.deleteGainAutomationPoint(next,0,'volume',24);next=E.deleteGainAutomationPoint(next,0,'volume',1234567);assert.deepEqual(next.tracks[0].automation,[]);
});
test('invalid, duplicate, unsafe and foreign-target edits are transactional',()=> {
  const source=E.addGainAutomationPoint(base(),0,'volume',{frame:9,value:1}),before=structuredClone(source);
  for(const point of [{frame:9,value:2},{frame:-1,value:2},{frame:0.5,value:2},{frame:Number.MAX_SAFE_INTEGER+1,value:2},{frame:10,value:Infinity},{frame:10,value:4.01},{frame:10,value:NaN},{frame:10,value:1,other:1},null]) assert.throws(()=>E.addGainAutomationPoint(source,0,'volume',point));
  assert.throws(()=>E.addGainAutomationPoint(source,0,'missing',{frame:10,value:1}));
  assert.throws(()=>E.deleteGainAutomationPoint(source,0,'volume',8));assert.deepEqual(source,before);
  for(const patch of [{parameter:'pan'},{interpolation:'linear'},{points:[]},{effect_id:'missing'}]) { const bad=structuredClone(source);Object.assign(bad.tracks[0].automation[0],patch);assert.ok(E.validate(bad));assert.throws(()=>E.editEffect(bad,0,'volume',{gain:1})); }
});
test('Rust gain bounds accept 16384 session points and reject overflow and excessive lanes',()=> {
  const source=base();source.tracks[0].automation=[{effect_id:'volume',parameter:'gain',interpolation:'step',points:Array.from({length:16384},(_,frame)=>({frame,value:1}))}];
  assert.equal(E.validate(source),null);assert.throws(()=>E.addGainAutomationPoint(source,0,'volume',{frame:16384,value:1}),/16384/);
  const bad=base();bad.tracks[0].automation=Array(17).fill(source.tracks[0].automation[0]);assert.match(E.validate(bad),/16 gain/);
});
test('gain editing leaves legacy schema unchanged and unrelated note edits preserve automation',()=> {
  for(const schema of [3,4,9,10,11]) {
    const source=base();source.schema_version=schema;if(![10,11].includes(schema)) delete source.tracks[0].mixer;
    const next=E.addGainAutomationPoint(source,0,'volume',{frame:Number.MAX_SAFE_INTEGER,value:2});assert.equal(next.schema_version,schema);
    const moved=E.moveClip(next,0,next.tracks[0].clips[0].id,111);assert.deepEqual(moved.tracks[0].automation,next.tracks[0].automation);
  }
});
test('real point handlers add, update, delete and undo through the checked callback',async()=> {
  const s=setup(),original=structuredClone(s.current);
  input(s,'new automation frame').value='12345';input(s,'new automation value').value='1.23456789';
  await input(s,'new automation value').fire('keydown',{key:'Enter'});
  assert.deepEqual(s.current.tracks[0].automation[0].points,[{frame:12345,value:1.23456789}]);assert.deepEqual(s.history.undoTarget(s.current),original);
  input(s,'12345 automation frame').value='12346';await click(s,'12345 update automation point');assert.equal(s.current.tracks[0].automation[0].points[0].frame,12346);
  await click(s,'12346 delete automation point');assert.deepEqual(s.current.tracks[0].automation,[]);
});
test('invalid drafts, failed replacement and locks preserve session and show local errors',async()=> {
  const s=setup(),original=structuredClone(s.current),frame=input(s,'new automation frame');
  frame.value='';await click(s,'new add automation point');assert.deepEqual(s.current,original);assert.equal(frame.value,'');assert.match(s.errors.at(-1),/frames/);
  const status=s.container.children.at(-1);assert.equal(status.textContent,s.errors.at(-1));
  s.view.setState({locked:true});frame.value='99';await click(s,'new add automation point');assert.equal(s.edits,0);assert.equal(frame.disabled,true);assert.equal(status.textContent,s.errors.at(-1));
  s.view.setState({locked:false});s.fail=true;await click(s,'new add automation point');assert.deepEqual(s.current,original);assert.equal(frame.value,'99');assert.match(status.textContent,/not saved/);
  s.fail=false;await click(s,'new add automation point');assert.equal(s.current.tracks[0].automation[0].points[0].frame,99);assert.equal(s.container.children.at(-1).textContent,'');
});
test('redraw preserves pending drafts and error while untouched saved frames remain exact',async()=> {
  const s=setup();input(s,'new automation frame').value='123457';await click(s,'new add automation point');
  const value=input(s,'123457 automation value');value.value='5';await value.fire('input');await click(s,'123457 update automation point');
  s.view.render(s.current);assert.equal(input(s,'123457 automation value').value,'5');assert.match(s.container.children.at(-1).textContent,/0 to 4/);
  assert.equal(s.current.tracks[0].automation[0].points[0].frame,123457);
});
test('host rebuilding instances retains other drafts and errors, clears submitted draft before capture, restores failure draft',async()=> {
  const doc={createElement:tag=>new Node(tag,doc)};
  let current=E.addGainAutomationPoint(base(),0,'volume',{frame:10,value:1}),view,container,fail=false,captured;
  const errors=[];
  const mount=draftState=> {
    container=doc.createElement('section');
    view=Automation.create(container,{editor:E,trackIndex:0,effectId:'volume',draftState,onError:e=>errors.push(e),onEdit:async next=> {
      captured=view.getDraftState();
      if(fail) return false;
      current=next; mount(captured); return true;
    }});
    view.render(current);
  };
  mount();
  const s={get container(){return container;}};
  input(s,'10 automation value').value='5';await input(s,'10 automation value').fire('input');await click(s,'10 update automation point');
  assert.match(view.getDraftState().message,/0 to 4/);
  const state=view.getDraftState();mount(state);state.drafts[0][1].value='999';
  assert.equal(input(s,'10 automation value').value,'5');assert.match(container.children.at(-1).textContent,/0 to 4/);
  input(s,'new automation frame').value='100';await input(s,'new automation frame').fire('input');
  await click(s,'new add automation point');
  assert.deepEqual(captured.drafts,[[10,{frame:10,value:'5'}]]);
  assert.equal(input(s,'10 automation value').value,'5');assert.equal(input(s,'new automation frame').value,0);
  assert.equal(current.tracks[0].automation[0].points.at(-1).frame,100);
  input(s,'10 automation value').value='2';await input(s,'10 automation value').fire('input');fail=true;
  await click(s,'10 update automation point');
  assert.deepEqual(captured.drafts,[]);assert.equal(view.getDraftState().drafts[0][1].value,'2');
  assert.equal(current.tracks[0].automation[0].points[0].value,1);assert.match(view.getDraftState().message,/not saved/);
  fail=false;await click(s,'10 update automation point');assert.equal(current.tracks[0].automation[0].points[0].value,2);
  assert.deepEqual(view.getDraftState(),{drafts:[],message:''});
});
