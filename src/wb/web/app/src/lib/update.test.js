import test from 'node:test';
import assert from 'node:assert/strict';
import { DISMISSED_KEY, dismiss, dismissedVersion, shouldShow, waitForVersion } from './update.js';

test('the notice shows a newer version until it is dismissed', () => {
  const info = {available: true, latest: '0.4.0', current: '0.3.0'};
  assert.equal(shouldShow(info, null), true);
  assert.equal(shouldShow(info, '0.4.0'), false);
  assert.equal(shouldShow(info, '0.3.5'), true);
  assert.equal(shouldShow({available: false, latest: '0.3.0'}, null), false);
  assert.equal(shouldShow(null, null), false);
});

test('dismissal survives a storage that throws', () => {
  const values = new Map();
  const store = {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
  dismiss(store, '0.4.0');
  assert.equal(values.get(DISMISSED_KEY), '0.4.0');
  assert.equal(dismissedVersion(store), '0.4.0');
  const broken = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }};
  assert.equal(dismissedVersion(broken), null);
  dismiss(broken, '0.4.0');
  assert.equal(dismissedVersion(null), null);
});

test('waiting for the restarted hub tolerates errors and gives up', async () => {
  let clock = 0;
  const sleep = async ms => { clock += ms; };
  const now = () => clock;
  const answers = [new Error('down'), '0.3.0', '0.4.0'];
  const read = async () => { const answer = answers.shift(); if (answer instanceof Error) throw answer; return answer; };
  assert.equal(await waitForVersion(read, '0.4.0', {sleep, now}), true);
  assert.equal(clock, 6000);
  clock = 0;
  assert.equal(await waitForVersion(async () => '0.3.0', '0.4.0', {sleep, now, timeout: 10000}), false);
});
