const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../app/web/static/page.js'), 'utf8');
function page() {
  // Four equally likely paths. Only A=yes, B=yes collects 100 and files.
  // Holding either judgment at Jev's 50%, the other ranges from 0 to 50 collected.
  const nodes = ['A', 'B'].map(key => ({
    key, jev: [0.5, 0.5], branches: ['no', 'yes'],
    atoms: { d: { no: { collected: 0, petition_p: 0 }, yes: { collected: 50, petition_p: 0.5 } } },
    series: { no: { collected: [0] }, yes: { collected: [50] } },
  }));
  const elements = {};
  const ctx = vm.createContext({
    D: { nodes, dates: ['2024-05-15'], event: {
      daily: { collected_mean: [25], contractual: [100] }, monthly: [{ month: '2024-05' }],
    } },
    S: { overrides: {}, sel: { A: 1, B: 1 } }, N: 2,
    TB: { figures: { full: { collected: 25, petition_p: 0.25 } } },
    SK: {}, SER: {}, dragBase: {},
    $: id => elements[id] ||= { value: 50 },
    onChange() {}, rebuildCloseups() {},
  });
  for (const name of ['jevDist', 'dist', 'selB']) {
    vm.runInContext(source.match(new RegExp(`^  const ${name} = .*`, 'm'))[0], ctx);
  }
  for (const name of ['withBranch', 'tbExpect', 'effOf', 'bindCloseup', 'tbSeries']) {
    vm.runInContext(source.match(new RegExp(`^  function ${name}\\([^]*?^  }`, 'm'))[0], ctx);
  }
  ctx.bindCloseup(0, 'a');
  ctx.bindCloseup(1, 'b');
  const drag = (id, value) => {
    const slider = elements[`${id}-slider`];
    slider.onpointerdown(); slider.value = value; slider.oninput(); slider.onchange();
    ctx.tbSeries();
  };
  const collected = () => vm.runInContext('tbExpect(i => dist(i), "collected")', ctx);
  return { ctx, elements, drag, collected };
}

test('changing another judgment restores the first, including the daily and monthly collections', () => {
  const { ctx, drag, collected } = page();
  drag('a', 100);
  assert.equal(collected(), 50);
  drag('b', 100);
  assert.deepEqual(Object.keys(ctx.S.overrides), ['B']);
  assert.equal(collected(), 50); // 100 would combine overrides; 75 would omit their interaction.
  assert.equal(ctx.S.event.daily.collected_mean[0], 50);
  assert.equal(ctx.S.event.monthly[0].collected, 50);
  drag('a', 0);
  assert.deepEqual(Object.keys(ctx.S.overrides), ['A']);
  assert.equal(collected(), 0);
});

test('another judgment comparison holds all other answers at Jev, even with a live override', () => {
  const { ctx, drag } = page();
  drag('a', 100);
  const e = ctx.effOf(1);
  assert.equal(e.lo, 0);
  assert.equal(e.hi, 50);
  assert.equal(e.at, 25);
  assert.equal(e.flo, 0);
  assert.equal(e.fhi, 0.5);
  assert.equal(ctx.effOf(1, [0.5, 0.5]).at, 25);
});

test('reset restores Jev in the figures and series', () => {
  const { ctx, elements, drag, collected } = page();
  drag('b', 0);
  elements['b-reset'].onclick({ preventDefault() {} });
  ctx.tbSeries();
  assert.equal(collected(), 25);
  assert.equal(ctx.S.event.daily.collected_mean[0], 25);
  assert.deepEqual(Object.keys(ctx.S.overrides), []);
});

test('the existing path-based page retains its joint overrides', () => {
  const { ctx, drag } = page();
  ctx.TB = null;
  drag('a', 100); drag('b', 0);
  assert.deepEqual(Object.keys(ctx.S.overrides), ['A', 'B']);
});
