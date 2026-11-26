import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import { api, validateApiPath, validateNextPath } from './src/api.js';
import { validateServerUrl } from './src/config.js';

async function loadCliHarness({ config, apiImpl, setConfigImpl } = {}) {
  const actions = new Map();
  const requests = [];
  const savedConfigs = [];
  let exitCode;
  class Command {
    command(name) {
      const child = new Command();
      child.path = [this.path, name.split(' ')[0]].filter(Boolean).join(' ');
      return child;
    }
    name() { return this; }
    description() { return this; }
    version() { return this; }
    option() { return this; }
    action(fn) { actions.set(this.path, fn); return this; }
    parse() { return this; }
  }
  const color = new Proxy(x => x, { get: () => color });
  const oraFactory = () => {
    const spinner = {
      text: '',
      start() { return spinner; },
      succeed() { return spinner; },
      fail() { return spinner; },
      stop() { return spinner; },
    };
    return spinner;
  };
  const proc = {
    argv: [],
    env: {},
    stdin: { isTTY: false },
    get exitCode() { return exitCode; },
    set exitCode(v) { exitCode = v; },
  };
  const context = vm.createContext({
    console: { log() {} },
    process: proc,
    URL,
    URLSearchParams,
    setTimeout: (fn) => { fn(); return 0; },
    clearTimeout() {},
  });
  const source = await readFile(new URL('./bin/smsly.js', import.meta.url), 'utf8');
  const module = new vm.SourceTextModule(source, {
    context,
    importModuleDynamically: async (specifier) => {
      if (specifier === 'ora') {
        const mod = new vm.SyntheticModule(['default'], function () {
          this.setExport('default', oraFactory);
        }, { context });
        await mod.link(() => {});
        await mod.evaluate();
        return mod;
      }
      throw new Error(`Unexpected dynamic import: ${specifier}`);
    },
  });
  const exports = {
    commander: { Command },
    chalk: { default: color },
    '../src/config.js': {
      getConfig: () => config ?? { serverUrl: 'https://example.invalid', token: 'test' },
      setConfig: (data) => {
        savedConfigs.push(JSON.parse(JSON.stringify(data)));
        if (setConfigImpl) setConfigImpl(data);
      },
    },
    '../src/api.js': {
      api: async (...args) => {
        requests.push(args);
        if (apiImpl) return apiImpl(...args);
        return args[2].startsWith('/api/v1/services/?page_size=100')
          ? { results: [{ id: 'service-123', name: 'Demo' }] }
          : {};
      },
    },
  };
  await module.link(name => new vm.SyntheticModule(Object.keys(exports[name]), function () {
    for (const [key, value] of Object.entries(exports[name])) this.setExport(key, value);
  }, { context }));
  await module.evaluate();
  return { actions, requests, savedConfigs, getExitCode: () => exitCode };
}

async function runEnv(pairs) {
  const { actions, requests } = await loadCliHarness();
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

test('api uses Bearer auth for sk_, smsly_, and eyJ... JWT tokens', async () => {
  const originalFetch = globalThis.fetch;
  const seenAuth = [];
  globalThis.fetch = async (_url, opts) => {
    seenAuth.push(opts.headers.Authorization);
    return { ok: true, json: async () => ({}) };
  };
  try {
    const jwtToken = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig';
    await api('https://example.invalid', 'sk_test_123', '/api/v1/services/');
    await api('https://example.invalid', 'smsly_abc', '/api/v1/services/');
    await api('https://example.invalid', jwtToken, '/api/v1/services/');
    await api('https://example.invalid', 'legacy_drf_token', '/api/v1/services/');
    assert.deepEqual(seenAuth, [
      'Bearer sk_test_123',
      'Bearer smsly_abc',
      `Bearer ${jwtToken}`,
      'Token legacy_drf_token',
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('safeId rejects path traversal and invalid resource IDs in CLI actions', async () => {
  const { actions, requests, getExitCode } = await loadCliHarness({
    apiImpl: async () => ({ results: [{ id: '../etc/passwd', name: 'BadSvc' }] }),
  });
  await actions.get('deploy')('BadSvc');
  assert.equal(getExitCode(), 1);
  assert.equal(requests.length, 1);
});

test('URL scheme guard blocks non-loopback http:// and non-http schemes unless allowed', async () => {
  assert.equal(validateServerUrl('https://cloud.example.com/'), 'https://cloud.example.com');
  assert.equal(validateServerUrl('http://localhost:8090/'), 'http://localhost:8090');
  assert.equal(validateServerUrl('http://127.0.0.1:8090/'), 'http://127.0.0.1:8090');
  assert.throws(() => validateServerUrl('http://remote.example.com'), /Refusing plaintext http/);
  assert.throws(() => validateServerUrl('ftp://remote.example.com'), /Unsupported URL scheme/);
  assert.equal(validateServerUrl('http://remote.example.com', true), 'http://remote.example.com');

  await assert.rejects(
    () => api('http://remote.example.com', 'tok', '/api/v1/services/'),
    /Refusing plaintext http/,
  );

  // Saved config URL scheme enforcement via loadValidatedConfig in smsly.js
  const { actions, requests, getExitCode } = await loadCliHarness({
    config: { serverUrl: 'http://remote.example.com', token: 'test', allowInsecureHttp: false },
  });
  await assert.rejects(() => actions.get('services')(), /Refusing plaintext http/);
  assert.equal(requests.length, 0);
  assert.equal(getExitCode(), undefined);
});

test('pagination next_path guard enforces /api/ prefix, rejects // and authority confusion', async () => {
  assert.equal(
    validateNextPath('https://example.invalid/api/v1/services/?page=2', 'https://example.invalid'),
    '/api/v1/services/?page=2',
  );
  assert.throws(
    () => validateNextPath('https://attacker.invalid/api/v1/services/', 'https://example.invalid'),
    /cross-origin/i,
  );
  assert.throws(
    () => validateNextPath('//attacker.invalid/api/v1/services/', 'https://example.invalid'),
    /cross-origin/i,
  );
  assert.throws(
    () => validateNextPath('@attacker.invalid/api/v1/services/', 'https://example.invalid'),
    /Invalid API path/,
  );
  assert.throws(() => validateApiPath('//attacker.invalid/api/v1/services/'), /Invalid API path/);
  assert.throws(() => validateApiPath('@attacker.invalid/api/v1/services/'), /Invalid API path/);

  const { actions, getExitCode } = await loadCliHarness({
    apiImpl: async () => ({
      results: [{ id: 'svc-1', name: 'One' }],
      next: 'https://attacker.invalid/api/v1/services/?page=2',
    }),
  });
  await actions.get('services')();
  assert.equal(getExitCode(), 1);
});

test('ecosystem scan captures initial plan_id and ecosystem deploy fetches plan detail in-memory', async () => {
  const { actions, requests, savedConfigs, getExitCode } = await loadCliHarness({
    config: { serverUrl: 'https://example.invalid', token: 'test' },
    apiImpl: async (_url, _tok, path, method) => {
      if (path === '/api/v1/cloud/ecosystem/scan/' && method === 'POST') {
        return { task_id: 'scan-task-1', plan_id: 'plan-abc-123', status: 'scanning' };
      }
      if (path === '/api/v1/cloud/ecosystem/task_status/?task_id=scan-task-1') {
        return {
          status: 'SUCCESS',
          result: { total_repos_scanned: 2, services: [{ name: 'api', stack: 'node', port: 3000, deploy_order: 1 }] },
        };
      }
      if (path === '/api/v1/cloud/ecosystem/plans/plan-abc-123/') {
        return { id: 'plan-abc-123', plan: { services: [{ name: 'api', port: 3000 }] } };
      }
      if (path === '/api/v1/cloud/ecosystem/deploy/' && method === 'POST') {
        return { task_id: 'deploy-task-1' };
      }
      if (path === '/api/v1/cloud/ecosystem/task_status/?task_id=deploy-task-1') {
        return {
          status: 'SUCCESS',
          result: { total: 1, queued: 1, skipped: 0, services: [{ name: 'api', status: 'queued' }] },
        };
      }
      return {};
    },
  });

  await actions.get('ecosystem scan')();
  assert.equal(getExitCode(), undefined);
  assert.equal(savedConfigs.length, 1);
  assert.deepEqual(savedConfigs[0].lastPlan, { plan_id: 'plan-abc-123' });

  // Now deploy using the saved plan_id
  const deployHarness = await loadCliHarness({
    config: savedConfigs[0],
    apiImpl: async (_url, _tok, path, method, body) => {
      if (path === '/api/v1/cloud/ecosystem/plans/plan-abc-123/') {
        return { id: 'plan-abc-123', plan: { services: [{ name: 'api', port: 3000 }] } };
      }
      if (path === '/api/v1/cloud/ecosystem/deploy/' && method === 'POST') {
        assert.deepEqual(JSON.parse(JSON.stringify(body)), {
          plan_id: 'plan-abc-123',
          plan: { services: [{ name: 'api', port: 3000 }] },
        });
        return { task_id: 'deploy-task-1' };
      }
      if (path === '/api/v1/cloud/ecosystem/task_status/?task_id=deploy-task-1') {
        return {
          status: 'SUCCESS',
          result: { total: 1, queued: 1, skipped: 0, services: [{ name: 'api', status: 'queued' }] },
        };
      }
      return {};
    },
  });
  await deployHarness.actions.get('ecosystem deploy')();
  assert.equal(deployHarness.getExitCode(), undefined);
  assert.equal(deployHarness.requests[0][2], '/api/v1/cloud/ecosystem/plans/plan-abc-123/');
  assert.equal(deployHarness.requests[1][2], '/api/v1/cloud/ecosystem/deploy/');
  assert.equal(deployHarness.savedConfigs[0].lastPlan, undefined);
});
