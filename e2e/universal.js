// End-to-end browser test: real backend + real agent process + Vite UI. `claude` is the TEST DOUBLE backend/tests/fake_claude.py.
// Run: cd e2e && NODE_PATH=$(npm root -g) node universal.js   (needs playwright + python deps + frontend npm install)
const { chromium } = require('playwright'); const { spawn } = require('child_process'); const path = require('path'); const fs = require('fs'); const os = require('os');
const R = path.resolve(__dirname, '..'); const W = fs.mkdtempSync(path.join(os.tmpdir(), 'cit-e2e-'));
fs.mkdirSync(`${W}/bin`); fs.mkdirSync(`${W}/proj`); fs.mkdirSync(`${W}/data`);
fs.writeFileSync(`${W}/bin/claude`, `#!/bin/sh\nexec python3 ${R}/backend/tests/fake_claude.py "$@"\n`, { mode: 0o755 });
fs.writeFileSync(`${W}/proj/app.py`, "print('hello')\n# TODO fix\n");
const env = { ...process.env, PATH: `${W}/bin:${process.env.PATH}`, CIT_DATA_DIR: `${W}/data`, CIT_API_TOKEN: 'devtoken', CIT_LOCAL_ROOTS: W };
const procs = []; const sp = (cmd, args, opts) => { const p = spawn(cmd, args, { env, detached: true, stdio: ['ignore', 'pipe', 'pipe'], ...opts }); procs.push(p); return p; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const agent = sp('python3', [`${R}/agent/cit_agent.py`, '--root', `${W}/proj`, '--allow-terminal', '--allow-write', '--allow-claude', '--port', '8766', '--bind', '127.0.0.1']);
  let aout = ''; agent.stdout.on('data', d => aout += d);
  sp('python3', ['-m', 'app.main'], { cwd: `${R}/backend` });
  sp('npx', ['vite', '--host', '127.0.0.1'], { cwd: `${R}/frontend`, env: { ...env, VITE_API_TOKEN: 'devtoken' } });
  await sleep(5000);
  const code = /Pairing code:\s+(\d{8})/.exec(aout)[1]; const fp = /Fingerprint:\s+([0-9A-F ]+)/.exec(aout)[1].trim();
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1360, height: 900 } }); const errs = [];
  p.on('pageerror', e => errs.push(e.message)); p.on('console', m => m.type() === 'error' && errs.push(m.text()));
  p.on('dialog', d => d.accept());
  await p.goto('http://127.0.0.1:1420');
  const nav = t => p.click(`nav >> text="${t}"`);
  // Quick connect
  await nav('Quick Connect'); await p.click('button.card:has-text("Portable USB Technician Agent")');
  await p.fill('input[placeholder^="169.254"]', '127.0.0.1:8766'); await p.click('button:has-text("Detect methods")'); await p.waitForSelector('text=Technician agent');
  await p.fill('text=Name >> xpath=../input', 'Client laptop'); await p.fill('text=Owner >> xpath=../input >> nth=0', 'Sara'); await p.fill('text=Owner contact >> xpath=../input', 'sara@example.com');
  await p.fill('textarea', 'Owner agreed in writing today'); await p.click('button:text-is("Register")');
  await p.fill('text=Pairing code (8 digits) >> xpath=../input', code); await p.fill('text=Fingerprint (64 hex) >> xpath=../input', fp);
  await p.click('button:has-text("Pair & test")'); await p.waitForSelector('text=Connection test', {timeout: 8000}).catch(async () => { console.log('UI ERROR:', await p.locator('.err').allInnerTexts(), 'inputs:', await p.locator('input').evaluateAll(i => i.map(x => x.value))); console.log(aout.slice(-600)); throw new Error('pairing failed'); });
  const stages = await p.locator('table:has-text("identity") tr').allInnerTexts(); console.log('connection test:', stages.map(s => s.replace(/\s+/g, ' ').slice(0, 70)));
  await p.screenshot({ path: 'quick.png' });
  // Project
  await nav('Development Projects'); await p.waitForSelector('summary');
  await p.fill('text=Name >> xpath=../input >> nth=0', 'demo'); await p.fill('text=Folder on the target >> xpath=../input', `${W}/proj`);
  await p.selectOption('text=Stack >> xpath=../select', 'other'); await p.click('button:text-is("Add")'); await p.waitForSelector('text=app.py');
  await p.click('td:has-text("app.py")'); await p.waitForSelector('textarea[rows="14"]');
  await p.click('button:text-is("git status")').catch(() => {}); 
  await p.screenshot({ path: 'projects.png' });
  // AI workspace: remote Claude
  await nav('AI Workspace'); await p.fill('textarea', 'FAKE:replace # TODO fix=>DONE in app.py');
  await p.selectOption('text=Execution mode >> xpath=../select', 'remote'); await p.click('button:has-text("Prepare plan")');
  await p.waitForSelector('text=Run Claude Code ON THE TARGET');
  const approve = p.locator('tr:has-text("Run Claude Code ON THE TARGET") button:has-text("Approve")'); console.log('approve disabled before confirm:', await approve.isDisabled());
  await p.check('tr:has-text("Run Claude Code ON THE TARGET") input[type=checkbox]'); await approve.click();
  await p.click('button:has-text("Execute approved steps")'); await p.waitForSelector('text=remote Claude Code exited 0', { timeout: 30000 });
  await sleep(1500); await p.screenshot({ path: 'task.png', fullPage: true });
  console.log('file now:', require('fs').readFileSync(`${W}/proj/app.py`, 'utf8').trim().split('\n').pop());
  // Servers + features + stop
  await nav('Settings'); await p.waitForSelector('text=Feature status'); await p.screenshot({ path: 'features.png', fullPage: true });
  await p.click('button:has-text("EMERGENCY STOP")'); await p.waitForSelector('text=All sessions revoked'); await sleep(800);
  console.log('agent after stop:', /SESSION ENDED/.test(aout) ? aout.split('\n').filter(l => /SESSION ENDED/.test(l))[0] : 'STILL RUNNING');
  await nav('Overview'); await p.click('text=العربية'); await p.screenshot({ path: 'rtl2.png' }); console.log('errors:', JSON.stringify(errs.filter(e => !/Failed to load resource/.test(e))));
  await b.close();
})().catch(e => { console.error('FAIL', e.message.split('\n').slice(0, 3).join(' | ')); process.exitCode = 1; }).finally(() => procs.forEach(pr => { try { process.kill(-pr.pid); } catch {} }));
