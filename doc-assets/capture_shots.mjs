// 自控 Chromium 批量截图：一个进程内启动浏览器 → 登录 → 逐页 2x 截图 → 关闭。
//
// 为什么不用 agent-browser：其 daemon 在本机已不稳定（CDP captureScreenshot 超时、
// 后续报 "Resource temporarily unavailable"）。本脚本自己 spawn Playwright 缓存里的
// chromium headless shell，直连 CDP，行为完全可控。
//
// 用法：node capture.mjs <任务文件.json> [onlyId]
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const BIN = process.env.CHROME_BIN;
const PORT = Number(process.env.CDP_PORT || 9333);
const OUT = process.env.OUT_DIR || '/tmp/shots';
const tasksFile = process.argv[2];
const only = process.argv[3];

const tasks = JSON.parse(fs.readFileSync(tasksFile, 'utf8'));
const list = only ? tasks.filter(t => t.id === only) : tasks;

const chrome = spawn(BIN, [
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${OUT}/prof`,
  '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars',
  '--window-size=1600,900',
  '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows',
  '--disable-background-timer-throttling',
  '--run-all-compositor-stages-before-draw',
  '--disable-features=CalculateNativeWinOcclusion',
  '--force-device-scale-factor=1',
  'about:blank',
], { stdio: ['ignore', 'ignore', 'pipe'], detached: false });
chrome.on('error', e => { console.error('spawn 失败:', e.message); process.exit(1); });
let chromeErr = '';
chrome.stderr.on('data', d => { chromeErr += d.toString(); });

// ---- 等 CDP 端口就绪 ----
async function waitPort(ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return await r.json();
    } catch {}
    await new Promise(r => setTimeout(r, 300));
  }
  throw new Error('CDP 端口未就绪\n' + chromeErr.slice(-500));
}

async function pageTarget() {
  const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const p = l.find(t => t.type === 'page');
  if (!p) throw new Error('无 page target: ' + JSON.stringify(l.map(t => t.type)));
  return p;
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    let id = 0; const pend = new Map();
    const events = [];
    ws.addEventListener('message', e => {
      let m; try { m = JSON.parse(typeof e.data === 'string' ? e.data : Buffer.from(e.data).toString()); } catch { return; }
      if (m.id && pend.has(m.id)) {
        const { res, rej } = pend.get(m.id); pend.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error).slice(0, 160))) : res(m.result);
      } else if (m.method) events.push(m);
    });
    ws.addEventListener('error', () => reject(new Error('ws error')));
    ws.addEventListener('open', () => resolve({
      ws,
      send: (method, params = {}, ms = 30000) => new Promise((res, rej) => {
        const i = ++id; pend.set(i, { res, rej });
        ws.send(JSON.stringify({ id: i, method, params }));
        setTimeout(() => { if (pend.has(i)) { pend.delete(i); rej(new Error('TIMEOUT ' + method)); } }, ms);
      }),
      events,
    }));
  });
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = [];
const leftoverPages = [];

try {
  const ver = await waitPort();
  console.log('CDP 就绪:', ver.Browser);
  const target = await pageTarget();
  const cdp = await connect(target.webSocketDebuggerUrl);
  const { send, events } = cdp;
  await send('Page.enable');
  await send('Runtime.enable');

  const evalJs = async (expr, awaitPromise = false, ms = 20000) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise }, ms);
    if (r.exceptionDetails) throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 200));
    return r.result.value;
  };
  const waitLoad = async (ms = 30000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (events.some(e => e.method === 'Page.loadEventFired')) { events.length = 0; return; }
      await sleep(200);
    }
  };
  const goto = async (url, ms = 30000) => {
    events.length = 0;
    await send('Page.navigate', { url });
    await waitLoad(ms);
    await sleep(800);
  };

  // ---- 登录 ----
  await goto('http://192.168.21.195:8088/');
  await evalJs(`localStorage.setItem('flowhub_token','${process.env.FH_TOKEN}');
    localStorage.setItem('flowhub_user', ${JSON.stringify(process.env.FH_USER)});
    'ok'`);
  await goto('http://192.168.21.195:8088/');
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 2, mobile: false });
  await sleep(1500);
  const r0 = await evalJs(`JSON.stringify({dpr:devicePixelRatio,w:innerWidth,h:innerHeight,aside:!!document.querySelector('aside'),txt:document.body.innerText.slice(0,40)})`);
  console.log('登录后:', r0);

  // ---- 逐页截图 ----
  for (const t of list) {
    const t0 = Date.now();
    try {
      if (t.click) {
        const cr = await evalJs(`(function(){const b=[...document.querySelectorAll('aside button')].find(x=>x.innerText.trim().indexOf(${JSON.stringify(t.click)})===0);
          if(!b) return 'NOTFOUND'; b.click(); return 'ok';})()`);
        if (cr === 'NOTFOUND') throw new Error('菜单未找到: ' + t.click);
      }
      if (t.js) await evalJs(t.js);
      // 先等页面渲染稳定，再做真实鼠标点击
      await sleep(t.wait || 2200);
      // 真实鼠标点击：React 的 onClick 有时不响应 element.click()，用 Input 域派发真事件
      if (t.realClick) {
        const boxStr = await evalJs(`(function(){var e=${t.realClick};if(!e)return 'NULL';
          e.scrollIntoView({block:'center',behavior:'instant'});
          var r=e.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2),w:Math.round(r.width),h:Math.round(r.height)})})()`);
        if (boxStr === 'NULL') throw new Error('点击目标未找到');
        const { x, y, w, h } = JSON.parse(boxStr);
        if (x < 1 || y < 1 || x > 1599 || y > 899) throw new Error(`点击坐标越界 ${x},${y} (元素 ${w}x${h})`);
        await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
        await sleep(90);
        await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
        await sleep(90);
        await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
        console.log(`    realClick @${x},${y} on ${w}x${h}`);
        await sleep(t.afterClick || 3200);
      }
      await sleep(600);
      await evalJs('window.scrollTo(0,0)');
      await sleep(300);
      // 脱敏：把人员姓名等宽替换为 ●，不改变排版宽度；截图后 React 若重渲染也不影响已出图
      if (process.env.MASK_NAMES) {
        const mn = process.env.MASK_NAMES.split(',').filter(Boolean);
        const masked = await evalJs(`(function(names){var cnt=0;
          function mk(s){ return s.replace(/[\\s\\S]/g, function(c){
            return /[\\u4e00-\\u9fff\\u3000-\\u303f\\uff00-\\uffef]/.test(c) ? '\\u25cf' : '*'; }); }
          function fix(t){
            for(var i=0;i<names.length;i++){ var s=names[i]; if(s && t.indexOf(s)>=0) t=t.split(s).join(mk(s)); }
            t = t.replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}/g, function(m){ return '*'.repeat(m.length); });
            return t; }
          function w(n){ if(n.nodeType===3){ var t=n.nodeValue,o=t; t=fix(t); if(t!==o){ n.nodeValue=t; cnt++; } }
            else if(n.nodeType===1){ var g=n.tagName; if(g==='SCRIPT'||g==='STYLE') return;
              for(var j=0;j<n.childNodes.length;j++) w(n.childNodes[j]); } }
          w(document.body);
          var fs=[].slice.call(document.querySelectorAll('input,textarea'));
          for(var k=0;k<fs.length;k++){ var v=fs[k].value||''; var nv=fix(v); if(nv!==v) fs[k].value=nv; }
          return cnt; })(${JSON.stringify(mn)})`);
        if (t.mask !== false) {
          await sleep(350);
          const left = await evalJs(`(function(names){var t=document.body.innerText;var hit=[];
            for(var i=0;i<names.length;i++){ if(names[i]&&t.indexOf(names[i])>=0) hit.push(names[i]); }
            var em=(t.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}/g)||[]).slice(0,3);
            var ph=(t.match(/1[3-9]\\d{9}/g)||[]).slice(0,3);
            return JSON.stringify({hit:hit,em:em,ph:ph});})(${JSON.stringify(mn)})`);
          const { hit: rest, em, ph } = JSON.parse(left);
          const flags = [];
          if (rest.length) flags.push('残留姓名:' + rest.join(','));
          if (em.length) flags.push('邮箱:' + em.join(','));
          if (ph.length) flags.push('手机号:' + ph.join(','));
          console.log(`    masked nodes=${masked}` + (flags.length ? '  ⚠ ' + flags.join(' | ') : '  ✓ 干净'));
          if (flags.length) leftoverPages.push(t.id + ' -> ' + flags.join(' | '));
        }
      }
      await sleep(150);
      const shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, 40000);
      const buf = Buffer.from(shot.data, 'base64');
      fs.writeFileSync(path.join(OUT, t.id + '.png'), buf);
      console.log(`✓ ${t.id.padEnd(18)} ${((Date.now()-t0)/1000).toFixed(1)}s ${buf.readUInt32BE(16)}x${buf.readUInt32BE(20)} ${(buf.length/1024).toFixed(0)}KB  ${t.desc || ''}`);
    } catch (e) {
      failures.push(t.id + ': ' + e.message);
      console.log(`✗ ${t.id.padEnd(18)} ${e.message}`);
    }
  }
} catch (e) {
  console.error('致命错误:', e.message);
  failures.push('fatal: ' + e.message);
} finally {
  try { chrome.kill('SIGKILL'); } catch {}
}
console.log('\n完成。失败:', failures.length ? failures : '无');
console.log('脱敏残留页:', leftoverPages.length ? leftoverPages : '无');
process.exit(0);
