# Cloudflare Tunnel demo

Public demo via a single frontend tunnel. No `README.md` change.

## Why "Blocked request" happens

Vite blocks unknown `Host` headers by default. A `*.trycloudflare.com`
URL is not `localhost`, so `web/vite.config.ts` must set
`host: true` + `allowedHosts: true` and bind `0.0.0.0:5173`.

## Recommended: one tunnel (no CORS, no rebuild)

```bash
./scripts/run_all.sh
# new terminal:
cloudflared tunnel --url http://localhost:5173
# open https://<xxx>.trycloudflare.com
```

Browser calls relative `/api/...`, Vite proxies to
`http://127.0.0.1:8000` locally. Backend tunnel not needed.

## Optional: two tunnels (public backend URL)

```bash
cloudflared tunnel --url http://localhost:8000  # -> https://<api>.trycloudflare.com
```

Then:

1. `web/.env`: `VITE_API_BASE_URL=https://<api>.trycloudflare.com/api`, restart Vite.
2. `api/.env`: add frontend host to `FRONTEND_ORIGINS`, restart API.
3. CORS already allows `*.trycloudflare.com` /
   `*.cfargotunnel.com` via `allow_origin_regex` in `api/app/main.py`.

## Troubleshooting

- `502/504`: `cloudflared` can't reach localhost — keep `run_all.sh`
  services running, check ports 5173/8000.
- Still `Blocked`: ensure you pulled the `allowedHosts` change and
  restarted `npm run dev`.
- `Failed to fetch` / CORS: single-tunnel avoids this; for two tunnels
  check `FRONTEND_ORIGINS` and browser devtools origin.
