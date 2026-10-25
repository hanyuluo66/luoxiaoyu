/**
 * Headless UI check for the AI大肥鱼 console.
 *
 * Connects to an already-running headless Chrome (started with
 * --remote-debugging-port), clicks every sidebar page, screenshots it,
 * and reports any JS exception or console error.
 *
 * Usage:
 *   chrome --headless=new --no-sandbox --remote-debugging-port=9333 \
 *          --user-data-dir=.tmp/chrome-uicheck --window-size=1680,1150 http://127.0.0.1:8799/
 *   node tools/uicheck.mjs --port 9333 --out .tmp/shots
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const args = process.argv.slice(2);
const getArg = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};

const PORT = Number(getArg('--port', '9333'));
const OUT_DIR = getArg('--out', '.tmp/shots');

const VIEWS = [
  ['overview', '概览'],
  ['private', '私聊'],
  ['group', '群聊'],
  ['rules', '规则'],
  ['ai', 'AI'],
  ['rhythm', '节奏'],
  ['logs', '日志'],
  ['system', '系统'],
];

mkdirSync(OUT_DIR, { recursive: true });

let ws = null;
const pending = new Map();
let nextId = 1;
const errors = [];
const consoleErrors = [];

function send(method, params = {}) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }
    }, 20000);
  });
}

async function waitForChrome() {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const targets = await res.json();
      const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch {
      /* not up yet */
    }
    await sleep(400);
  }
  throw new Error('Chrome DevTools endpoint never came up');
}

async function main() {
  const wsUrl = await waitForChrome();
  ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });

  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
      return;
    }
    if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params.exceptionDetails;
      errors.push(d.exception?.description || d.text);
    }
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      consoleErrors.push(
        msg.params.args.map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 300),
      );
    }
    if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error') {
      consoleErrors.push(String(msg.params.entry.text).slice(0, 300));
    }
  });

  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.enable');

  // Wait until the SPA has fetched bootstrap and rendered.
  for (let i = 0; i < 40; i++) {
    const r = await send('Runtime.evaluate', {
      expression: `document.querySelectorAll('.nav-item').length`,
      returnByValue: true,
    });
    if ((r.result?.value || 0) >= 8) break;
    await sleep(300);
  }
  await sleep(1200);

  const summary = [];
  for (const [view, label] of VIEWS) {
    const clicked = await send('Runtime.evaluate', {
      expression: `(() => { const el = document.querySelector('[data-view="${view}"]');
        if (!el) return 'missing';
        el.click();
        return el.className; })()`,
      returnByValue: true,
    });
    await sleep(700);

    const active = await send('Runtime.evaluate', {
      expression: `(() => { const el = document.querySelector('[data-view="${view}"]');
        const pane = document.querySelector('.page.active, .page[style*="block"], main .page');
        return { cls: el ? el.className : '', text: (document.body.innerText || '').length }; })()`,
      returnByValue: true,
    });

    const shot = await send('Page.captureScreenshot', { format: 'png' });
    const file = `${OUT_DIR}/page-${view}.png`;
    writeFileSync(file, Buffer.from(shot.data, 'base64'));

    summary.push({
      view,
      label,
      click: clicked.result?.value,
      activeClass: active.result?.value?.cls,
      textLen: active.result?.value?.text,
      file,
    });
    console.log(
      `  ${view.padEnd(9)} click=${clicked.result?.value} active=${String(active.result?.value?.cls).slice(0, 40)} text=${active.result?.value?.text} -> ${file}`,
    );
  }

  const bodyText = await send('Runtime.evaluate', {
    expression: 'document.body.innerText',
    returnByValue: true,
  });
  writeFileSync(`${OUT_DIR}/body-text.txt`, bodyText.result?.value || '', 'utf8');

  console.log('\n=== JS exceptions ===');
  console.log(errors.length ? errors.join('\n---\n') : '(none)');
  console.log('=== console errors ===');
  console.log(consoleErrors.length ? [...new Set(consoleErrors)].join('\n') : '(none)');
  console.log('=== pages captured ===', summary.length);

  ws.close();
  process.exit(errors.length ? 2 : 0);
}

main().catch((err) => {
  console.error('UICHECK FAILED:', err.message);
  process.exit(1);
});
