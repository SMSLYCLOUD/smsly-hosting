import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

async function runEnv(pairs) {
  const actions = new Map();
  const requests = [];
  class Command {
    command(name) { const child = new Command(); child.path = [this.path, name.split(' ')[0]].filter(Boolean).join(' '); return child; }
    name() { return this; } description() { return this; } version() { return this; }
    option() { return this; } action(fn) { actions.set(this.path, fn); return this; }
    parse() { return this; }
  }
  const color = new Proxy(x => x, { get: () => color });
  const context = vm.createContext({ console: { log() {} }, process: { argv: [] } });
  const source = await readFile(new URL('./bin/smsly.js', import.meta.url), 'utf8');
  const module = new vm.SourceTextModule(source, { context });
  const exports = {
    commander: { Command }, chalk: { default: color },
    '../src/config.js': { getConfig: () => ({ serverUrl: 'https://example.invalid', token: 'test' }), setConfig() {} },
    '../src/api.js': { api: async (...args) => { requests.push(args); return args[2] === '/api/v1/services/' ? { results: [{ id: 'service-123', name: 'Demo' }] } : {}; } },
  };
  await module.link(name => new vm.SyntheticModule(Object.keys(exports[name]), function () { for (const [key, value] of Object.entries(exports[name])) this.setExport(key, value); }, { context }));
  await module.evaluate();
  await actions.get('env set')('demo', pairs);
  return JSON.parse(JSON.stringify(requests));
}

test('env set posts flat variables to the dedicated endpoint preserving equals and empty values', async () => {
  const calls = await runEnv(['TOKEN=a=b', 'EMPTY=']);
  assert.deepEqual(calls.slice(1), [
    ['https://example.invalid', 'test', '/api/v1/services/service-123/env_vars/', 'POST', { key: 'TOKEN', value: 'a=b' }],
    ['https://example.invalid', 'test', '/api/v1/services/service-123/env_vars/', 'POST', { key: 'EMPTY', value: '' }],
  ]);
});

test('env set does not send assignments missing equals', async () => {
  assert.equal((await runEnv(['BROKEN'])).length, 1);
});
