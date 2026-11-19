import {test} from 'node:test';
import assert from 'node:assert/strict';
import {dropText, dropTo, priceAt} from './dropTo.ts';

test('drop needed is proportional to the ratio', () => {
  assert.equal(dropTo(160, 130)!.toFixed(4), '-18.7500');
  assert.equal(dropTo('130', 130), 0);
  assert.ok(dropTo(125, 130)! > 0);
  assert.equal(dropTo(null, 130), null);
  assert.equal(dropTo(0, 130), null);
});

test('price at a target level and labels', () => {
  assert.equal(priceAt(100, 160, 130)!.toFixed(2), '81.25');
  assert.equal(priceAt(null, 160, 130), null);
  assert.equal(dropText(dropTo(160, 130)), '再跌 18.8%');
  assert.equal(dropText(dropTo(120, 130)), '已低於');
  assert.equal(dropText(null), '—');
});
