"""Offline Chromium-extension tests. No real session, Moodle or external service is contacted."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "browser_helper"


@unittest.skipUnless(shutil.which("node"), "Node is needed only for extension regression tests")
class BrowserHelperTests(unittest.TestCase):
    def run_js(self, script):
        subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)

    def test_manifest_permissions_and_javascript_syntax(self):
        manifest = json.loads((HELPER / "manifest.json").read_text())
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["host_permissions"], ["https://moodle.ffhs.ch/*"])
        self.assertEqual(manifest["optional_host_permissions"], ["https://*/*"])
        self.assertNotIn("cookies", manifest["permissions"])
        for path in HELPER.glob("*.js"):
            subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)

    def test_worker_limits_capture_to_moodle_and_binds_downloads_to_the_current_tab(self):
        self.run_js(r"""
const assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs');
const data={}, downloads=[], listeners={}; let active=1;
const tabs={1:{id:1,url:'https://moodle.ffhs.ch/mod/kalvidres/view.php?id=312',title:'Video 1'},
  2:{id:2,url:'https://other.example/course',title:'Other'}};
const ctx={URL,Map,Promise,crypto:require('node:crypto').webcrypto,Date,
 chrome:{runtime:{id:'our-extension',getURL:p=>'chrome-extension://our-extension/'+p,
 onMessage:{addListener:f=>listeners.message=f}},
 tabs:{get:async id=>tabs[id],query:async()=>[tabs[active]],create:async p=>{listeners.created=p;},
 onUpdated:{addListener:f=>listeners.updated=f},onRemoved:{addListener:f=>listeners.removed=f}},
 webRequest:{onHeadersReceived:{addListener:f=>listeners.capture=f}},
 storage:{session:{get:async key=>({[key]:data[key]}),set:async value=>Object.assign(data,value),
 remove:async key=>{delete data[key];}}},downloads:{download:async item=>{downloads.push(item);return 11;}}}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('browser_helper/worker.js','utf8'),ctx);
const sender={id:'our-extension',url:'chrome-extension://our-extension/popup.html'};
async function capture(url,tabId=1,mime='video/mp4'){
 await ctx.capture({url,tabId,statusCode:200,responseHeaders:[{name:'Content-Type',value:mime}]});}
(async()=>{
 await capture('https://cdn.example/lecture.mp4');await capture('https://cdn.example/lecture.mp4');
 await capture('https://cdn.example/outside.mp4',2);
 await capture('https://www.youtube.com/watch?v=test');await capture('https://r3.googlevideo.com/stream.mp4');
 await capture('https://youtube.com.evil.example/video.ts');await capture('https://cdn.example/frag.m4s');
 await capture('https://cdn.example/master.m3u8',1,'application/vnd.apple.mpegurl');
 await capture('https://cdn.example/signed?id=secret-signature',1,'video/mp4');
 let result=await ctx.handle({type:'list'},sender);assert.equal(result.items.length,3);
 assert.ok(result.items.every(item=>!('url' in item)));
 const file=result.items.find(item=>item.kind==='file');
 await ctx.handle({type:'download',id:file.id},sender);
 assert.equal(downloads.length,1);assert.ok(downloads[0].filename.startsWith('FFHS_Moodle/cm_312/'));
 assert.equal(downloads[0].url,'https://cdn.example/lecture.mp4');
 await assert.rejects(ctx.handle({type:'download',id:'not-captured'},sender));
 await assert.rejects(ctx.handle({type:'list'},{...sender,id:'foreign'}));
 const stream=result.items.find(item=>item.kind==='stream');
 await assert.rejects(ctx.handle({type:'download',id:stream.id},sender));
 await ctx.handle({type:'hls',id:stream.id},sender);
 assert.ok(listeners.created.url.startsWith('chrome-extension://our-extension/download.html#'));
 assert.equal(Object.keys(data).filter(key=>key.startsWith('job_')).length,1);
 tabs[1].url='https://moodle.ffhs.ch/mod/page/view.php?id=313';
 await assert.rejects(ctx.handle({type:'download',id:file.id},sender));
 active=2;await assert.rejects(ctx.handle({type:'list'},sender));active=1;
 listeners.removed(1);await new Promise(resolve=>setTimeout(resolve,0));assert.equal(data.tab_1,undefined);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_hls_plans_master_audio_and_real_local_segment_playlists(self):
        self.run_js(r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const ctx={URL};vm.createContext(ctx);vm.runInContext(fs.readFileSync('browser_helper/hls.js','utf8'),ctx);
const h=ctx.FFHS_HLS,base='https://cdn.example/course/master.m3u8?signature=private';
const master=h.parse('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="English, main",DEFAULT=YES,URI="audio/list.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=900,AUDIO="a"\nhigh.m3u8\n'.replaceAll('\\n','\n'),base);
assert.equal(master.kind,'master');assert.equal(master.video,'https://cdn.example/course/high.m3u8');
assert.equal(master.audio,'https://cdn.example/course/audio/list.m3u8');
const leaf=h.parse('#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:4\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4,\nsegment-1.m4s?sig=private\n#EXT-X-DISCONTINUITY\n#EXTINF:2,\nsegment-2.m4s\n#EXT-X-ENDLIST\n'.replaceAll('\\n','\n'),base);
assert.equal(leaf.entries.length,3);assert.equal(leaf.entries[0].url,'https://cdn.example/course/init.mp4');
assert.ok(leaf.playlist.includes('#EXT-X-MAP:URI="init0.mp4"'));
assert.ok(leaf.playlist.includes('s000001.m4s'));assert.ok(!leaf.playlist.includes('private'));
assert.ok(!leaf.playlist.includes('https://'));assert.ok(leaf.playlist.includes('#EXT-X-ENDLIST'));
for(const bad of [
 '#EXTM3U\n#EXTINF:4,\na.ts',
 '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST',
 '#EXTM3U\n#EXT-X-BYTERANGE:5@0\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST',
 '#EXTM3U\n#EXT-X-PRELOAD-HINT:TYPE=PART,URI="https://cdn.example/private"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST',
 '#EXTM3U\n#EXTINF:4,\nhttps://r1.googlevideo.com/a.ts\n#EXT-X-ENDLIST',
 '#EXTM3U\n#EXTINF:4,\nhttp://cdn.example/a.ts\n#EXT-X-ENDLIST',
 '#EXTM3U\n#EXTINF:4,\nhttps://127.0.0.1/a.ts\n#EXT-X-ENDLIST']){
 assert.throws(()=>h.parse(bad.replaceAll('\\n','\n'),base));}
""")

    def test_hls_download_waits_for_segments_before_publishing_playlists(self):
        self.run_js(r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const elements={start:{disabled:false},stop:{disabled:true},status:{textContent:''},progress:{value:0,max:1}};
for(const element of Object.values(elements))element.addEventListener=()=>{};
const calls=[],states=new Map(),changeListeners=new Set();let sequence=0,failSegment=true;
const data={'job_12345678-1234-1234-1234-123456789abc':{url:'https://cdn.example/list.m3u8',
 context:'cm_123',label:'Lecture',createdAt:Date.now()}};
const ctx={URL,Blob,Set,Date,TypeError,Promise,setInterval,clearInterval,
 location:{hash:'#12345678-1234-1234-1234-123456789abc'},document:{getElementById:id=>elements[id]},
 fetch:async url=>({ok:true,url,text:async()=> '#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\na.ts\n#EXTINF:2,\nb.ts\n#EXT-X-ENDLIST\n'.replaceAll('\\n','\n')}),
 chrome:{permissions:{contains:async()=>true},storage:{session:{get:async key=>({[key]:data[key]}),remove:async key=>{delete data[key];}}},
 downloads:{download:async item=>{calls.push(item);const id=++sequence;
 states.set(id,failSegment&&item.url.endsWith('/b.ts')?'interrupted':'complete');return id;},
 search:async({id})=>[{id,state:states.get(id)}],cancel:async()=>{},
 onChanged:{addListener:f=>changeListeners.add(f),removeListener:f=>changeListeners.delete(f)}}}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('browser_helper/hls.js','utf8'),ctx);
vm.runInContext(fs.readFileSync('browser_helper/download.js','utf8'),ctx);
(async()=>{await ctx.run();assert.equal(calls.length,2);assert.ok(!calls.some(item=>item.url.startsWith('blob:')));
 assert.ok(elements.status.textContent.includes('fallito'));assert.equal(changeListeners.size,0);
 failSegment=false;calls.length=0;await ctx.run();assert.equal(calls.length,3);
 assert.equal(calls[0].url,'https://cdn.example/a.ts');assert.equal(calls[1].url,'https://cdn.example/b.ts');
 assert.ok(calls[2].url.startsWith('blob:'));assert.ok(calls[2].filename.endsWith('/video.m3u8'));
 assert.ok(elements.status.textContent.startsWith('Completato:'));assert.equal(changeListeners.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_hls_network_needs_an_explicit_granted_host_permission(self):
        self.run_js(r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const elements={start:{},stop:{},status:{textContent:''},progress:{}};
for(const e of Object.values(elements))e.addEventListener=()=>{};
let fetched=0,downloaded=0;
const ctx={URL,Blob,Set,Date,TypeError,Promise,setInterval,clearInterval,
 location:{hash:'#12345678-1234-1234-1234-123456789abc'},document:{getElementById:id=>elements[id]},
 fetch:async()=>{fetched++;throw Error('Unexpected fetch');},chrome:{permissions:{contains:async()=>false},
 storage:{session:{get:async key=>({[key]:{url:'https://cdn.example/list.m3u8',context:'cm_1',label:'Clip',createdAt:Date.now()}})}},
 downloads:{download:async()=>{downloaded++;}}}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('browser_helper/hls.js','utf8'),ctx);
vm.runInContext(fs.readFileSync('browser_helper/download.js','utf8'),ctx);
(async()=>{await ctx.run();assert.equal(fetched,0);assert.equal(downloaded,0);
 assert.ok(elements.status.textContent.includes('Permesso mancante'));
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


if __name__ == "__main__":
    unittest.main()
