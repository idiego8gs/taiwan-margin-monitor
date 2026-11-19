// Dispatch the site's update workflow. The workflow itself decides whether a new trading day
// exists; repeated dispatches without new data keep the published version.
export async function dispatch(env, fetcher = fetch) {
  if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN secret is not set');
  const url = `https://api.github.com/repos/${env.REPOSITORY}/actions/workflows/${env.WORKFLOW}/dispatches`;
  const response = await fetcher(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'taiwan-margin-scheduler',
    },
    body: JSON.stringify({ref: env.REF}),
  });
  // GitHub answers 204 No Content when the run is queued.
  if (response.status !== 204) {
    throw new Error(`GitHub dispatch failed: ${response.status} ${(await response.text()).slice(0, 200)}`);
  }
  return response.status;
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatch(env).then(
      () => console.log(`dispatched ${env.WORKFLOW} for cron ${controller.cron}`),
      error => { console.error(String(error)); throw error; },
    ));
  },
};
