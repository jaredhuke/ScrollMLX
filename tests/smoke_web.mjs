// Headless smoke test for the Scroll web UI — loads static/index.html in jsdom with
// a stubbed backend and asserts it boots without errors and key surfaces exist.
// Run: node tests/smoke_web.mjs   (needs jsdom: `npm i --no-save jsdom`)
import { JSDOM } from 'jsdom';
import fs from 'fs';
import path from 'path';

const html = fs.readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), '..', 'static', 'index.html'), 'utf8');

const DATA = {
  '/health': { status: 'ok' },
  '/v1/providers': { providers: [] }, '/v1/keys': { providers: {} },
  '/v1/tools': { tools: [] }, '/v1/ledger': { summary: {}, entries: [] },
  '/v1/model': { primary: 'q' }, '/v1/standing': { user: [], learned: [], path: '/x' },
  '/v1/projects': { projects: [] }, '/v1/skills': { skills: [] },
  '/v1/sys': { cpu: 5, mem_pct: 5, thermal: 'nominal' },
  '/v1/models': { models: [], recommended: '', ram_gb: 16 },
  '/v1/onboard': { system: {}, keys: {}, recommendations: [] },
};
const f = (u) => { let b = {}; for (const k in DATA) if (u.startsWith(k)) { b = DATA[k]; break; }
  return Promise.resolve({ ok: true, json: () => Promise.resolve(b), text: () => Promise.resolve('') }); };

const errs = [];
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://127.0.0.1:8080/',
  beforeParse(w) {
    w.fetch = f; w.requestAnimationFrame = () => 0; w.cancelAnimationFrame = () => {};
    w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    w.scrollTo = () => {};
    w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get(t, p) {
      if (p === 'measureText') return () => ({ width: 0 });
      if (/Gradient|Pattern/.test(p)) return () => ({ addColorStop() {} });
      if (p === 'canvas') return { width: 56, height: 15 };
      return () => {};
    }});
    w.onerror = (m) => errs.push(String(m));
  },
});
const win = dom.window, doc = win.document;
win.console.error = (...a) => errs.push(a.join(' '));

await new Promise((r) => setTimeout(r, 500));

let fail = 0;
const ok = (c, l) => { console.log((c ? 'ok   ' : 'FAIL ') + l); if (!c) fail++; };

ok(!!doc.getElementById('msg-in'), 'composer present');
ok(!!doc.getElementById('send-btn'), 'send button present');
ok(typeof win.eval('typeof send') === 'string' && win.eval('typeof send') === 'function', 'send() defined');
['notify', 'openOnboard', 'renderCards', 'parseTargets', '_mdLite', 'checkHealth'].forEach((fn) =>
  ok(win.eval(`typeof ${fn}==='function'`), `${fn}() defined`));
const real = [...new Set(errs)].filter((e) => !/Could not parse CSS|Not implemented/.test(e));
ok(real.length === 0, `no console errors on boot (${real.length})`);
real.slice(0, 5).forEach((e) => console.log('   · ' + e.slice(0, 140)));

console.log(fail ? `\n${fail} check(s) FAILED` : '\nweb smoke passed');
process.exit(fail ? 1 : 0);
