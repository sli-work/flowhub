// 把 build_doc.py 生成的 HTML 导出成「原生 A4」PDF。
//
// 两个历史坑，都已在本脚本里绕开：
//   1) agent-browser 的 printToPDF 未开启 preferCSSPageSize，Chrome 会忽略 CSS 里的
//      `@page{size:A4;margin:0}`，改用 Letter 纸 + 0.4in 默认页边距，内容超宽时把整页
//      缩放（实测 ×0.9414）；再用 pymupdf 重排到 A4 又会二次缩放并留上下白边 —— 最终
//      内容只占 A4 宽 91.5%，观感偏小偏软。
//   2) agent-browser 的常驻 daemon 在本机会进入不可用状态（captureScreenshot 超时、
//      报 "Resource temporarily unavailable"）。
// 所以本脚本自己 spawn Playwright 缓存里的 chromium headless shell，直连 CDP，
// 显式指定 A4 英寸尺寸 + margin 0，一步输出 1:1 原生 A4。
//
// PDF 的 base64 体积超过 WebSocket 单帧上限，故用 transferMode=ReturnAsStream
// 配合 IO.read 分块读取。
//
// 用法：
//   node export_pdf.mjs [输入.html] [输出.pdf]
//   （默认：../FlowHub企业服务案例介绍.html → ../FlowHub企业服务案例介绍.pdf）
//   CHROME_BIN 可覆盖浏览器路径。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';

const here = path.dirname(new URL(import.meta.url).pathname);
const htmlPath = path.resolve(process.argv[2] || path.join(here, '../FlowHub企业服务案例介绍.html'));
const outPath = path.resolve(process.argv[3] || path.join(here, '../FlowHub企业服务案例介绍.pdf'));
if (!fs.existsSync(htmlPath)) { console.error('找不到输入 HTML:', htmlPath); process.exit(1); }

function findChrome() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  const base = path.join(os.homedir(), 'Library/Caches/ms-playwright');
  if (!fs.existsSync(base)) throw new Error('未找到 Playwright 浏览器缓存，请设置 CHROME_BIN');
  const candidates = [];
  for (const d of fs.readdirSync(base)) {
    const shell = path.join(base, d, 'chrome-headless-shell-mac-arm64/chrome-headless-shell');
    const chrome = path.join(base, d, 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
    if (d.startsWith('chromium_headless_shell-') && fs.existsSync(shell)) candidates.push({ d, p: shell });
    else if (d.startsWith('chromium-') && fs.existsSync(chrome)) candidates.push({ d, p: chrome });
  }
  if (!candidates.length) throw new Error('未找到可用的 Chromium 二进制');
  candidates.sort((a, b) => a.d.localeCompare(b.d));
  return candidates[candidates.length - 1].p;
}

const BIN = findChrome();
const PORT = Number(process.env.CDP_PORT || 9433);
const A4_W_IN = 210 / 25.4;
const A4_H_IN = 297 / 25.4;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const chrome = spawn(BIN, [
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${fs.mkdtempSync(path.join(os.tmpdir(), 'pdfprof-'))}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--window-size=1600,900', '--run-all-compositor-stages-before-draw',
  'about:blank',
], { stdio: ['ignore', 'ignore', 'ignore'] });
chrome.on('error', e => { console.error('启动浏览器失败:', e.message); process.exit(1); });

async function waitPort(ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) return; } catch {}
    await sleep(300);
  }
  throw new Error('CDP 端口未就绪');
}

try {
  await waitPort();
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pend = new Map(); let loaded = false;
  ws.addEventListener('message', e => {
    let m; try { m = JSON.parse(typeof e.data === 'string' ? e.data : Buffer.from(e.data).toString()); } catch { return; }
    if (m.id && pend.has(m.id)) {
      const { res, rej } = pend.get(m.id); pend.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error).slice(0, 200))) : res(m.result);
    } else if (m.method === 'Page.loadEventFired') loaded = true;
  });
  const send = (method, params = {}, ms = 180000) => new Promise((res, rej) => {
    const i = ++id; pend.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method, params }));
    setTimeout(() => { if (pend.has(i)) { pend.delete(i); rej(new Error('TIMEOUT ' + method)); } }, ms);
  });
  await new Promise(r => ws.addEventListener('open', r));
  await send('Page.enable');

  const url = 'file://' + htmlPath.split('/').map(encodeURIComponent).join('/');
  await send('Page.navigate', { url });
  for (let i = 0; i < 200 && !loaded; i++) await sleep(250);
  // 等内嵌 base64 图片解码完成（每页 16 张 2600px JPEG）
  const t0 = Date.now();
  let imgs = { total: 0, done: 0 };
  do {
    imgs = JSON.parse((await send('Runtime.evaluate', {
      expression: `JSON.stringify({total:document.images.length,done:[...document.images].filter(i=>i.complete&&i.naturalWidth>0).length})`,
      returnByValue: true,
    })).result.value);
    if (imgs.done >= imgs.total) break;
    await sleep(400);
  } while (Date.now() - t0 < 60000);
  console.log(`图片解码 ${imgs.done}/${imgs.total}  (${((Date.now()-t0)/1000).toFixed(1)}s)`);
  await sleep(1200);

  const { stream } = await send('Page.printToPDF', {
    paperWidth: A4_W_IN, paperHeight: A4_H_IN,
    marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0,
    printBackground: true, preferCSSPageSize: false, scale: 1, imageQuality: 100,
    transferMode: 'ReturnAsStream',
  });
  const chunks = [];
  for (let n = 0; n < 20000; n++) {
    const r = await send('IO.read', { handle: stream, size: 262144 });
    if (r.data) chunks.push(Buffer.from(r.data, r.base64Encoded ? 'base64' : 'utf8'));
    if (r.eof) break;
  }
  fs.writeFileSync(outPath, Buffer.concat(chunks));
  console.log('OK →', path.basename(outPath), (fs.statSync(outPath).size / 1024 / 1024).toFixed(2), 'MB');
  ws.close();
} catch (e) {
  console.error('导出失败:', e.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill('SIGKILL'); } catch {}
}
process.exit(process.exitCode || 0);
