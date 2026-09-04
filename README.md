# RepeaterMock Login Service (Render)

Deploys a Flask API on Render that solves Cloudflare Turnstile and logs
into repeatermock.com using nodriver (real Chrome).

## Endpoints
- `GET /health` — health check
- `POST /login` — trigger login (returns run_id)
- `GET /status/<run_id>` — poll run status + logs
- `GET /cookies` — get latest cookies

## Why Render?
Render uses AWS datacenter IPs which have better reputation than GitHub
Actions runners. Combined with nodriver (real Chrome), Cloudflare Turnstile
is more likely to render and solve.
