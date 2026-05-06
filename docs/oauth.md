# OAuth-protected MCP servers

`slack_agents.tools.mcp_http_oauth` connects to MCP servers that require OAuth 2.1
authentication, with **per-Slack-user tokens**: each user authenticates separately,
and the agent uses that user's access token when calling tools on their behalf.

This complements `slack_agents.tools.mcp_http`, which is for servers that issue
long-lived API keys you put in YAML headers. If your MCP server speaks the MCP
authorization spec (Dynamic Client Registration + auth-code + PKCE), use
`mcp_http_oauth`.

## Configuration

```yaml
tools:
  my-mcp:
    type: slack_agents.tools.mcp_http_oauth
    url: "https://my-server.example.com/mcp"
    allowed_functions: [".*"]
    init_retries: [5, 10, 30]    # optional
    auth_timeout: 300             # optional, seconds, default 300
```

Only `url` and `allowed_functions` are required. There is intentionally no
`client_id` / `client_secret` / `scopes` field — the provider performs Dynamic
Client Registration against the MCP server's authorization server, registers
with whatever scopes the server's PRM document advertises, and discovers
runtime scopes through standard 401/403 step-up challenges.

## Required environment variables

These are validated at startup. If any `mcp_http_oauth` provider is configured
and any of these are missing or malformed, the agent refuses to start with a
single consolidated error message.

| Variable | Required | Default | Description |
|---|---|---|---|
| `OAUTH_PUBLIC_URL` | yes | — | Externally reachable base URL of this agent process. Must be `https://`, or `http://` with a loopback host (`localhost`, `127.0.0.1`, `[::1]`) for local dev. |
| `OAUTH_SECRET_KEY` | yes | — | Root key for HKDF; ≥32 bytes after base64 decode. Used to sign OAuth state tokens and encrypt refresh tokens at rest. |
| `OAUTH_BIND_HOST` | no | `0.0.0.0` | Interface the in-process callback listener binds to. |
| `OAUTH_BIND_PORT` | no | `8080` | TCP port for the callback listener. |

### Generating `OAUTH_SECRET_KEY`

```bash
openssl rand -base64 32
```

or

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Treat this value like any other long-lived secret: keep it out of source
control, rotate it the same way you rotate database credentials. Rotating it
forces every user to re-authenticate but does not break the agent.

## Local development

OAuth callbacks need a URL the user's browser can reach. For local dev use a
tunnel (ngrok, cloudflared, tailscale funnel, etc.):

```bash
# Terminal 1 — start the tunnel pointing at your bind port:
ngrok http 8080
# → forwards https://abcd-1234.ngrok-free.app to localhost:8080

# Terminal 2 — set env vars and run the agent:
export OAUTH_PUBLIC_URL=https://abcd-1234.ngrok-free.app
export OAUTH_SECRET_KEY=$(openssl rand -base64 32)
slack-agents run agents/my-agent
```

If you'd rather not use a tunnel, you can run with
`OAUTH_PUBLIC_URL=http://localhost:8080` — the validator allows loopback
addresses over plain HTTP per RFC 8252.

## What a Slack user sees

1. They ask the bot to do something that needs OAuth-protected tools.
2. The bot replies with an ephemeral message in the same thread (visible only to
   them) containing an "Authenticate" button.
3. They click; the browser opens the upstream service's login page.
4. They log in and click Allow.
5. The browser shows "Authentication completed — you can close this tab and
   return to Slack."
6. Slack shows a brief "✅ Authenticated to *server*" ephemeral, the agent
   picks up the new token, runs tool discovery, and the conversation continues
   normally.

If they don't click within the configured `auth_timeout` (default 5 minutes),
the agent surfaces a "timed out — please try again" error and the tool call
ends. They can re-ask whenever they're ready and a fresh prompt appears.

If the upstream tool later requires additional permissions (e.g. they had read
access but are now trying to write), the same flow re-runs requesting the
broader scope. Most identity providers auto-collapse the consent screen if the
broader scope is a superset of what they've already approved.

If the user's account doesn't actually have the role the upstream needs (so
the IdP silently issues a token without the requested scope), the agent
detects this on the next tool call and surfaces a clear permission-denied
message naming the specific missing scope — rather than a generic error.

## How scopes work

Three scope-related decisions happen at different times, with different
sources of truth. Knowing which is which makes troubleshooting much easier.

### 1. DCR registration scope (one-time, per server)

When the agent first encounters an OAuth-protected MCP server, it does
Dynamic Client Registration with the server's authorization server. The
client registration declares **all the scopes this client could ever
legitimately request** — this is the catalog, not the per-call request.

The agent uses **PRM `scopes_supported`** as that catalog: it pre-fetches
`/.well-known/oauth-protected-resource` from the resource server and
registers with exactly that list. No heuristics, no extrapolation.

This means the **resource server's PRM document must advertise every scope
that any of its tools might ever require**, not just the default tier. If
PRM only advertises `mcp:foo:read` but a tool returns a 403 demanding
`mcp:foo:write`, the registered client was never permitted to request
`mcp:foo:write` and the step-up will fail with `invalid_scope`.

### 2. Per-request authorize scope (every tool call)

For each authorize request to the IdP (initial auth and step-up alike), the
agent computes the union of three sources:

```
authorize_scope =
    {openid, offline_access}                  # OIDC protocol baseline (always)
  ∪ scopes from the user's currently-cached token
  ∪ scopes the server hinted in `WWW-Authenticate scope=`
```

The OIDC baseline is added by the agent unconditionally — without `openid`
the IdP issues a non-OIDC token (no identity claims) and without
`offline_access` no refresh token is issued (forcing fresh auth on every
token expiry). The cached scopes preserve what's already been granted, so
step-up never accidentally narrows what the user has. The hint from
`WWW-Authenticate` is what the resource server *just* asked for, this call.

The resource server can be stateless: it can return either the cumulative
set the user now needs (`scope="mcp:foo:read mcp:foo:write"`) or just the
delta scope for this call (`scope="mcp:foo:write"`). The client merges with
its own state either way.

### 3. What the token actually grants (decided by the IdP)

After the user consents, the IdP issues a token with whatever scopes their
roles actually permit — it may silently drop scopes the user can't have.
The agent compares the post-step-up token's scope against the server's
demand. If a required scope wasn't granted, the agent surfaces a clean
permission-denied error naming the specific missing scope — instead of
retrying forever or surfacing the upstream 403 verbatim.

### Resource server expectations

For the agent to behave correctly out of the box, your MCP server should:

- **PRM `/.well-known/oauth-protected-resource`** should advertise every
  scope its tools might require, including step-up scopes (e.g. read AND
  write AND admin), not just the default tier.
- **401 responses** (no token at all) should include `scope=` with the
  minimum needed to use the resource (typically the read-equivalent).
- **403 responses** (token with insufficient scope) should include
  `WWW-Authenticate: Bearer error="insufficient_scope" scope="…"` per RFC
  9470, naming the scope(s) needed for this specific call. The server can
  return either the cumulative set or the delta — the client tolerates both.
- **OIDC scopes** (`openid`, `offline_access`) don't need to appear in
  either header — the client always adds them on its own.

## Token storage

Tokens are persisted via the agent's normal storage backend (SQLite or
Postgres) in two new tables:

- `oauth_tokens` — per (user_id, server_id) access token + encrypted refresh
  token, scopes, expiry.
- `oauth_clients` — per server_id Dynamic Client Registration result, shared
  across all users connecting to that server through this agent.

Refresh tokens are AES-GCM-encrypted at rest using a subkey derived from
`OAUTH_SECRET_KEY` via HKDF. Access tokens are short-lived and stored
plaintext (still in the private DB).

## Troubleshooting

**"Configuration error: ... OAUTH_PUBLIC_URL is not set"** — set the env vars
listed in the message and restart.

**"Authentication timed out"** — the user didn't click the link within
`auth_timeout`. They can re-ask the bot whenever they're ready.

**"<server> does not support dynamic client registration"** — the upstream
authorization server doesn't speak RFC 7591, or it has a Client Registration
Policy that rejects requests from your agent's host. Common Keycloak gates:

- *Trusted Hosts* policy — the realm admin must add your agent's
  externally-reachable host to the trusted-hosts list.
- A CDN/WAF in front of the IdP — some Cloudflare bot-management rules block
  anonymous DCR requests; the realm admin needs an exception for the
  `/clients-registrations/openid-connect` endpoint.

This provider is DCR-only by design. Static pre-registered client credentials
are not currently supported.

**`invalid_scope` on step-up after a successful first auth** — the
DCR-registered client doesn't have the requested scope in its allowed-request
list, even though it would have been included in the registration request.
This is Keycloak's *Allowed Client Scopes* policy under
`Realm Settings → Client Registration → Anonymous Access Policies`: the
realm silently filters DCR registration scope to a permitted subset. The
realm admin must add the missing scope (e.g. `mcp:foo:write`) to that
policy. Verify what the realm actually registered for your client by querying
the local DB:

```bash
sqlite3 /tmp/<agent>.slack-agents.db \
  "SELECT json_extract(metadata_json, '\$.scope') FROM oauth_clients WHERE server_id='<server>';"
```

If a scope is missing here, the policy filtered it out at DCR time.

**"This action cannot be run on `<server>`: your account does not have a role
granting the required scope"** — the IdP issued a token but silently dropped
the requested scope because the user's role doesn't include it. The user (or
their admin) needs to grant the missing scope at the role level. The agent
names the specific missing scope in the message.

**"You declined access"** — the user clicked Deny on the consent screen, or
the IdP returned `error=access_denied`. They can re-ask to retry.

**Tokens disappear after a key rotation** — expected. Rotating
`OAUTH_SECRET_KEY` invalidates all stored refresh tokens (the agent detects
this on the first read and deletes the row, then prompts for fresh auth on the
next call).

## Implementation notes

- The in-process callback listener runs alongside the Slack Bolt connection
  (same asyncio loop, same process). It only listens when at least one
  `mcp_http_oauth` provider is configured.
- The listener exposes exactly two routes: `/oauth/start/{signed_state}` and
  `/oauth/callback`. Anything else returns 404.
- OAuth state is signed (HMAC-SHA256) and includes a single-use nonce; replays
  are rejected.
- Restarting the agent during a pending auth flow drops that flow — the user
  re-asks and gets a fresh prompt. Persistent mid-flow recovery is intentionally
  not implemented.

### MCP SDK workarounds

The provider patches around four behaviors of the `mcp` Python SDK
(`mcp.client.auth`) at the time of writing. When the SDK addresses any of
these upstream, the corresponding shim can be removed:

1. **Pre-DCR with full PRM scope set.** The SDK's `async_auth_flow`
   overwrites `client_metadata.scope` with the runtime authorize scope
   (`get_client_metadata_scopes`) *before* running DCR. If we let the SDK do
   DCR, the registered client gets only "what's needed for the current
   operation," not the full catalog, and step-up later fails with
   `invalid_scope`. We do DCR ourselves with PRM's `scopes_supported`,
   persist the result, and the SDK's `if not self.context.client_info: …
   register …` branch is skipped.
2. **Discovery on every fresh `OAuthClientProvider`.** The SDK's 403 step-up
   path calls `_perform_authorization()` without first running protected-
   resource discovery, so a freshly constructed provider falls back to
   `urljoin(server_url, "/authorize")` (wrong when the AS is on a different
   host). We pre-populate `oauth.context.protected_resource_metadata` and
   `oauth_metadata` from a per-Provider cache.
3. **`token_expiry_time` not propagated from storage.** The SDK's
   `_initialize` loads `current_tokens` from storage but doesn't set
   `token_expiry_time`, so `is_token_valid()` returns True for any cached
   token regardless of actual expiry — meaning a stale token is sent at
   restart, the server returns 401, the SDK skips the refresh-token branch
   entirely, and the user is re-prompted. We force `_initialize` plus
   `update_token_expiry(tokens)` after construction so refresh works
   silently across restarts.
4. **Scope merge on `WWW-Authenticate`.** The SDK uses the server's
   `WWW-Authenticate scope=` value verbatim, dropping anything the cached
   token already had and the OIDC baseline. We attach an httpx response
   hook to every MCP request that augments the header in place to be the
   union of `{openid, offline_access}` ∪ cached-token scopes ∪ server-hinted
   scopes, so the SDK's verbatim use produces the correct cumulative set.

Each shim is annotated in the source with a comment explaining the SDK
behavior it works around.
