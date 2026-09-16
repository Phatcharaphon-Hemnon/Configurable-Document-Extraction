// Resolve caches from this file, so npm's INIT_CWD cannot redirect them outside the checkout.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const cache = fileURLToPath(new URL('../../.cache/', import.meta.url));
const result = spawnSync(process.execPath, [fileURLToPath(new URL('../node_modules/playwright/cli.js', import.meta.url)), ...process.argv.slice(2)], {
  cwd: fileURLToPath(new URL('../', import.meta.url)),
  stdio: 'inherit',
  env: {...process.env, PLAYWRIGHT_BROWSERS_PATH: `${cache}playwright`, npm_config_cache: `${cache}npm`, XDG_CACHE_HOME: `${cache}xdg`},
});
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
