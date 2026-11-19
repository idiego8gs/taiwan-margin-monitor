import {test} from 'node:test';
import assert from 'node:assert/strict';
import {dispatch} from './index.js';

const env = {GITHUB_TOKEN: 't', REPOSITORY: 'o/r', WORKFLOW: 'w.yml', REF: 'main'};

test('dispatches the workflow on main', async () => {
  let call;
  const status = await dispatch(env, async (url, init) => { call = {url, init}; return new Response(null, {status: 204}); });
  assert.equal(status, 204);
  assert.equal(call.url, 'https://api.github.com/repos/o/r/actions/workflows/w.yml/dispatches');
  assert.equal(JSON.parse(call.init.body).ref, 'main');
  assert.equal(call.init.headers.Authorization, 'Bearer t');
});

test('reports failures and a missing token', async () => {
  await assert.rejects(dispatch(env, async () => new Response('Bad credentials', {status: 401})), /401/);
  await assert.rejects(dispatch({...env, GITHUB_TOKEN: ''}), /not set/);
});
