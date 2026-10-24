const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const worker = fs.readFileSync(path.join(__dirname, '../browser-helper/background.js'), 'utf8');
function harness() {
 const state = {}; const opened = []; const removed = []; const calls = []; let receiver; let onAlarm;
 const context = {URL, Date, AbortSignal, console,
  fetch: async (url, options) => {
   calls.push({url, options});
   return {ok:true, json:async()=>url.endsWith('/next') ? {query:'rtx 4070',url:'https://www.ebay.com/sch/i.html?_nkw=rtx+4070&LH_BIN=1'} : {ok:true}};
  },
  chrome: {
   storage:{local:{get:async keys=>Object.fromEntries((typeof keys==='string'?[keys]:keys).map(k=>[k,state[k]])),set:async values=>Object.assign(state,values)}},
   tabs:{create:async opts=>{opened.push(opts);return {id:17};},update:async(id,opts)=>opened.push({id,...opts}),remove:async id=>removed.push(id)},
   alarms:{create:async()=>{},clear:async()=>{},onAlarm:{addListener:fn=>onAlarm=fn}},
   action:{setBadgeText:async()=>{},onClicked:{addListener:()=>{}}},
   runtime:{onMessage:{addListener:fn=>receiver=fn},onStartup:{addListener:()=>{}}},
  }
 };
 vm.createContext(context); vm.runInContext(worker,context);
 return {state,opened,removed,calls,context,message:(msg,sender)=>new Promise(resolve=>receiver(msg,sender,resolve)),alarm:()=>onAlarm({name:'dealfinder-search'})};
}
const local = {url:'http://127.0.0.1:8000/sources/ebay',tab:{id:3}};
const token = 'a'.repeat(43);
const ebay = {url:'https://www.ebay.com/sch/i.html?_nkw=rtx+4070&LH_BIN=1',tab:{id:17}};
test('pairing is restricted to the local setup page', async()=>{
 const h=harness(); const result=await h.message({type:'pair',token},{url:'https://evil.test',tab:{id:3}});
 assert.ok(result.error); assert.equal(h.opened.length,0); assert.equal(h.state.token,undefined);
});
test('one helper tab, server-issued query, no API credentials', async()=>{
 const h=harness(); assert.equal((await h.message({type:'pair',token},local)).ok,true);
 assert.equal(h.opened.length,2); assert.equal(h.opened[0].active,false);
 assert.equal(h.calls[0].options.headers['X-Dealfinder-Token'],token);
 h.alarm(); await new Promise(setImmediate); assert.equal(h.opened.length,2);
});
test('ordinary eBay tabs cannot capture or send listings',async()=>{
 const h=harness(); await h.message({type:'pair',token},local);
 assert.ok((await h.message({type:'ready'},{...ebay,tab:{id:99}})).error);
 const result=await h.message({type:'capture',html:'private'}, {...ebay,tab:{id:99}});
 assert.ok(result.error); assert.equal(h.calls.length,1);
});
test('successful capture closes only the helper tab',async()=>{
 const h=harness(); await h.message({type:'pair',token},local);
 assert.equal((await h.message({type:'ready'},ebay)).ok,true);
 const result=await h.message({type:'capture',html:'<ul>cards</ul>',captured_at:new Date().toISOString()},ebay);
 assert.equal(result.ok,true); assert.equal(h.state.pending,null); assert.deepEqual(h.removed,[17]);
 assert.equal(h.calls.length,2); assert.ok(h.calls[1].url.endsWith('/results'));
});
test('verification stops collection and preserves the tab',async()=>{
 const h=harness(); await h.message({type:'pair',token},local);
 await h.message({type:'capture',error:'Verification needed'},ebay);
 assert.equal(h.state.enabled,false); assert.equal(h.removed.length,0);
 assert.equal(JSON.parse(h.calls[1].options.body).error,'Verification needed');
});
test('app pause prevents new browser searches',async()=>{
 const h=harness(); h.context.fetch=async()=>({ok:true,json:async()=>({paused:true})});
 await h.message({type:'pair',token},local); assert.equal(h.opened.length,0);
});
test('expired pending search stops instead of accumulating tabs',async()=>{
 const h=harness(); await h.message({type:'pair',token},local);
 h.state.pending.started=Date.now()-100000; h.alarm(); await new Promise(setImmediate);
 assert.equal(h.state.enabled,false); assert.equal(h.opened.length,2);
});
