"""The display consumes intervals; derived ratios must not divide through zero."""
import subprocess
from pathlib import Path


def test_ratio_ranges_enclose_and_keep_zero_denominator_unknown():
    js = Path('app/web/static/page.js').read_text()
    helper = js[js.index('  function figureRange('):js.index('  function rangeHtml(')]
    subprocess.run(['node', '-e', '''
const assert = require('node:assert/strict');
let ranges = {collected: [8, 12], due: [10, 20], stayed: [0, 100], petition_p: [0, .2]};
const interval = (_, key) => ranges[key] || null;
''' + helper + '''
assert.deepEqual(figureRange([], 'rate'), [.4, 1.2]);
assert.deepEqual(figureRange([], 'stayed_if'), [-Infinity, Infinity]);
assert.equal(figureRange([], 'clawback_if'), null);
assert.deepEqual(figureRange([], 'collected'), [8, 12]);
ranges.due = [20, 40];
assert.deepEqual(figureRange([], 'rate'), [.2, .6]);
'''], check=True)
