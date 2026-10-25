/**
 * Functional save test: edits real form fields in the loaded console, clicks
 * 保存并生效, then reads the persisted config back from /api/bootstrap.
 *
 * Catches the classic silent failure where the UI's field name does not match
 * the backend model (pydantic would ignore unknown keys and keep the default).
 *
 * Usage: node tools/uicheck-save.mjs --port 9333
 */
import { setTimeout as sleep } from 'node:timers/promises';

const args = process.argv.slice(2);
const getArg = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};
const PORT = Number(getArg('--port', '9333'));

let ws = null;
const pending = new Map();
let nextId = 1;
const pageErrors = [];

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

async function evaluate(expression) {
  const res = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (res.exceptionDetails) {
    throw new Error(res.exceptionDetails.exception?.description || res.exceptionDetails.text);
  }
  return res.result?.value;
}

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!page) throw new Error('no page target');
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      return;
    }
    if (msg.method === 'Runtime.exceptionThrown') {
      pageErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    }
  });
  await send('Runtime.enable');

  let pass = 0;
  let fail = 0;
  const check = (name, ok, detail) => {
    if (ok) {
      pass++;
      console.log(`  [PASS] ${name}`);
    } else {
      fail++;
      console.log(`  [FAIL] ${name}  ${detail ?? ''}`);
    }
  };

  const readConfig = () =>
    evaluate(`fetch('/api/bootstrap').then(r => r.json()).then(d => d.config)`);

  // ---------------------------------------------------------------- baseline
  const before = await readConfig();
  console.log(`基线：temperature=${before.ai.temperature} private.chats=${JSON.stringify(before.private.chats)} rules=${before.rules.length}`);

  // ------------------------------------------------------ 1) scalar input save
  await evaluate(`document.querySelector('[data-view="ai"]').click()`);
  await sleep(500);
  await evaluate(`(() => {
    const el = document.querySelector('[data-bind="ai.temperature"]');
    if (!el) return 'missing';
    el.value = '1.5';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value;
  })()`);
  await evaluate(`document.querySelector('#btn-save-top').click()`);
  await sleep(1200);
  let cfg = await readConfig();
  check('滑动条 temperature 保存生效', Number(cfg.ai.temperature) === 1.5, `实际 ${cfg.ai.temperature}`);

  // -------------------------------------------------------- 2) tag list save
  await evaluate(`document.querySelector('[data-view="private"]').click()`);
  await sleep(500);
  const tagResult = await evaluate(`(() => {
    const box = document.querySelector('[data-tags="private.chats"]');
    if (!box) return 'missing-box';
    const input = box.querySelector('input');
    if (!input) return 'missing-input';
    input.value = '自动回复测试';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    return box.textContent.slice(0, 40);
  })()`);
  await evaluate(`document.querySelector('#btn-save-top').click()`);
  await sleep(1200);
  cfg = await readConfig();
  check(
    '会话名单标签保存生效',
    Array.isArray(cfg.private.chats) && cfg.private.chats.includes('自动回复测试'),
    `tagResult=${tagResult} chats=${JSON.stringify(cfg.private.chats)}`,
  );

  // ------------------------------------------------- 3) rule enable toggle save
  await evaluate(`document.querySelector('[data-view="rules"]').click()`);
  await sleep(500);
  const ruleCount = await evaluate(`document.querySelectorAll('.rule-card').length`);
  check('规则卡片渲染出来', ruleCount >= 1, `卡片数 ${ruleCount}`);
  if (ruleCount >= 1) {
    const beforeEnabled = (await readConfig()).rules[0].enabled;
    await evaluate(`(() => {
      const card = document.querySelector('.rule-card');
      const sw = card && card.querySelector('.switch input, input[type="checkbox"]');
      if (!sw) return 'no-switch';
      sw.click();
      return 'clicked';
    })()`);
    await evaluate(`document.querySelector('#btn-save-top').click()`);
    await sleep(1200);
    cfg = await readConfig();
    check(
      '规则启用开关保存生效',
      cfg.rules[0].enabled === !beforeEnabled,
      `之前 ${beforeEnabled} 之后 ${cfg.rules[0].enabled}`,
    );
  }

  // ------------------------------------------- 4) values survive a page reload
  await send('Page.enable');
  await send('Page.reload');
  await sleep(2500);
  cfg = await readConfig();
  check(
    '刷新后配置仍在新值上',
    Number(cfg.ai.temperature) === 1.5 &&
      cfg.private.chats.includes('自动回复测试'),
    JSON.stringify({ t: cfg.ai.temperature, c: cfg.private.chats }),
  );

  console.log('\n=== JS exceptions ===');
  console.log(pageErrors.length ? pageErrors.join('\n') : '(none)');
  console.log(`\n===== 通过 ${pass}，失败 ${fail} =====`);
  ws.close();
  process.exit(fail || pageErrors.length ? 2 : 0);
}

main().catch((err) => {
  console.error('SAVE TEST FAILED:', err.message);
  process.exit(1);
});
