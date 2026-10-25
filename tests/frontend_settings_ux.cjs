const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');

// Exercise component event handlers without a browser or a live console.
function mount(file, props, dependencies) {
  const slots = [];
  let index = 0;
  let effects = [];
  const react = {
    useCallback(callback) { return callback; },
    useState(initial) {
      const slot = index++;
      if (!(slot in slots)) slots[slot] = initial;
      return [slots[slot], next => { slots[slot] = typeof next === 'function' ? next(slots[slot]) : next; }];
    },
    useRef(initial) {
      const slot = index++;
      if (!(slot in slots)) slots[slot] = { current: initial };
      return slots[slot];
    },
    useEffect(callback, deps) {
      const slot = index++;
      const previous = slots[slot];
      if (!previous || deps.some((value, i) => value !== previous.deps[i])) {
        effects.push(() => {
          previous?.cleanup?.();
          slots[slot] = { deps, cleanup: callback() };
        });
      }
    },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(source, {
    exports, Promise, AbortController, setTimeout, clearTimeout,
    require(name) {
      if (name === 'react') return react;
      if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
      if (dependencies[name]) return dependencies[name];
      return new Proxy({ __esModule: true, default: name }, { get: (object, key) => object[key] || key });
    },
  });
  return {
    render() {
      index = 0;
      const tree = exports.default(props);
      const pending = effects;
      effects = [];
      pending.forEach(run => run());
      return tree;
    },
    unmount() { slots.forEach(slot => slot?.cleanup?.()); },
  };
}
function all(tree) {
  if (Array.isArray(tree)) return tree.flatMap(all);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...all(tree.props?.children)];
}
function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join('');
  return tree && typeof tree === 'object' ? text(tree.props?.children) : String(tree ?? '');
}
function button(tree, name) {
  const found = all(tree).find(node => node.props.type === 'button' && text(node) === name);
  assert.ok(found, `Missing button: ${name}`);
  return found;
}
const flush = () => new Promise(setImmediate);

test('profiles have an explicit create/cancel flow using the current connection, not API keys', async () => {
  const connection = { provider: 'groq', model: 'coding-model', baseUrl: '', reasoningMode: 'default' };
  let saved;
  const app = mount('frontend/src/components/settings/ModelProfiles.tsx', {
    value: connection, disabled: false, onApply() {},
  }, {
    '@/lib/ids': { newUuid: () => 'profile-id' },
    '@/lib/model-profiles': {
      readModelProfiles: () => [],
      modelConnection: value => ({ ...value }),
      saveModelProfile: profile => { saved = profile; return [profile]; },
    },
  });
  app.render();
  await flush();
  let tree = app.render();
  assert.equal(all(tree).some(node => node.props.id === 'profile-name'), false);
  button(tree, 'Save as new profile').props.onClick();
  tree = app.render();
  assert.equal(button(tree, 'Create profile').props.disabled, true);
  all(tree).find(node => node.props.id === 'profile-name').props.onChange({ target: { value: 'Coding' } });
  tree = app.render();
  button(tree, 'Create profile').props.onClick();
  tree = app.render();
  assert.equal(saved.name, 'Coding');
  assert.equal(saved.model, 'coding-model');
  assert.equal(saved.api_key, undefined);
  assert.equal(all(tree).some(node => node.props.id === 'profile-name'), false);
  button(tree, 'Save as new profile').props.onClick();
  button(app.render(), 'Cancel').props.onClick();
  assert.equal(all(app.render()).some(node => node.props.id === 'profile-name'), false);
  app.unmount();
});

test('saved keys hide replacement fields until requested, and cancel preserves the saved key', async () => {
  const requests = [];
  let pending = false;
  const app = mount('frontend/src/components/settings/ProviderKeySettings.tsx', {
    projectId: 'a'.repeat(32), provider: 'groq', baseUrl: '',
    onPendingChange: value => { pending = value; }, onBusyChange() {}, onChanged() {},
  }, {
    '@/lib/chat': { workspaceRequest: async (...args) => { requests.push(args); return { configured: true }; } },
  });
  app.render();
  await new Promise(resolve => setTimeout(resolve, 400));
  let tree = app.render();
  assert.equal(all(tree).some(node => node.props.id === 'provider-api-key'), false);
  button(tree, 'Replace key').props.onClick();
  tree = app.render();
  all(tree).find(node => node.props.id === 'provider-api-key').props.onChange({ target: { value: 'replacement' } });
  tree = app.render();
  assert.equal(pending, true);
  button(tree, 'Cancel').props.onClick();
  tree = app.render();
  assert.equal(pending, false);
  assert.ok(button(tree, 'Replace key'));
  assert.equal(requests.length, 1);
  assert.equal(requests[0][1], 'ai/key/status');
  app.unmount();
});

test('key save only refreshes discovery, and pending keys block saving defaults', async () => {
  let saves = 0;
  const defaults = { provider: 'groq', model: 'coding-model', baseUrl: '', reasoningMode: 'default' };
  const app = mount('frontend/src/components/settings/SettingsForm.tsx', {
    projectId: 'a'.repeat(32),
  }, {
    '@/lib/settings': {
      defaultSettings: defaults, readSettings: () => defaults,
      hasProjectSettings: () => false, validateSettings: () => null,
      saveSettings() { saves++; },
    },
  });
  app.render();
  await flush();
  let tree = app.render();
  let keys = all(tree).find(node => node.type === './ProviderKeySettings');
  keys.props.onChanged(true);
  assert.equal(saves, 0);
  tree = app.render();
  assert.equal(all(tree).find(node => node.props.id === 'model').props.refreshToken, 1);
  keys = all(tree).find(node => node.type === './ProviderKeySettings');
  keys.props.onPendingChange(true);
  tree = app.render();
  tree.props.onSubmit({ preventDefault() {} });
  assert.equal(saves, 0);
  assert.match(text(app.render()), /Save or cancel the API key entry/);
  keys.props.onPendingChange(false);
  tree = app.render();
  tree.props.onSubmit({ preventDefault() {} });
  assert.equal(saves, 1);
  app.unmount();
});
