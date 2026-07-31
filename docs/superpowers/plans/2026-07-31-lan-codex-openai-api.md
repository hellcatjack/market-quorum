# LAN Codex OpenAI API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing Codex OpenAI-compatible API to physical-LAN clients through trusted HTTPS, an exact Bearer API Key, and a fail-closed `192.168.1.0/24` Caddy boundary.

**Architecture:** Keep Codex Gateway on `127.0.0.1:8000` and add an `/openai/v1` edge route to the existing system Caddy. A dedicated root-owned environment file supplies one 256-bit key through a systemd drop-in that also removes the distribution unit's unsafe `--environ` flag; Caddy admits only two OpenAI paths from the physical LAN and strips the credential before proxying.

**Tech Stack:** Caddy 2.6.2, systemd system service, Bash, pytest, OpenAI Python SDK, FastAPI Gateway.

**Execution constraint:** Execute inline in the current `main` worktree. The user explicitly prohibited subagents and additional worktrees. Do not modify `TradingAgents/`.

---

## File map

- Modify `platform/tests/operations/test_deploy_config.py`: define the fail-closed Caddy, secret, installer, and systemd-drop-in contract.
- Modify `deploy/caddy/tradingng.caddy`: expose only authenticated physical-LAN OpenAI model and chat endpoints.
- Create `deploy/systemd/caddy-lan-openai.conf`: load the isolated key and replace Caddy's environment-dumping start command.
- Modify `scripts/install_public_caddy.sh`: create, validate, preserve, rotate, install, and roll back the key/drop-in safely.
- Modify `.gitignore`: exclude the live `.env.gateway-lan` secret.
- Modify `README.md`: document English client, installation, retrieval, and rotation instructions.
- Modify `README.zh-CN.md`: document the same operations separately in Chinese.
- Modify this plan: record completed verification and production acceptance without credentials or response content.

### Task 1: Lock the LAN security contract with failing tests

**Files:**
- Modify: `platform/tests/operations/test_deploy_config.py`
- Test: `platform/tests/operations/test_deploy_config.py`

- [x] **Step 1: Add the failing Caddy boundary test**

Add this test after `test_public_caddy_routes_only_to_loopback_platform_services`:

```python
def test_public_caddy_exposes_only_authenticated_physical_lan_codex_api():
    config = (ROOT / "deploy/caddy/tradingng.caddy").read_text()

    lan_handler = "handle /openai/* {"
    assert lan_handler in config
    assert config.index(lan_handler) < config.index("@noSession")
    assert "path /openai/v1/models /openai/v1/chat/completions" in config
    assert config.count("remote_ip 192.168.1.0/24") == 3
    assert 'header Authorization "Bearer {$CODEX_GATEWAY_LAN_API_KEY}"' in config
    assert "uri strip_prefix /openai" in config
    assert config.count("reverse_proxy 127.0.0.1:8000") == 1
    assert "header_up -Authorization" in config
    assert 'respond `{"error":{"message":"Unauthorized"' in config
    assert '"code":"invalid_api_key"}}` 401' in config
    assert 'respond `{"error":{"message":"Not found"' in config
    assert '"code":"not_found"}}` 404' in config
    assert 'respond `{"error":{"message":"Forbidden"' in config
    assert '"code":"lan_only"}}` 403' in config
    assert "192.168.201.0/24" not in config
    assert "/openai/internal/status" not in config
```

In `test_public_caddy_routes_only_to_loopback_platform_services`, replace:

```python
assert "127.0.0.1:8000" not in config
```

with:

```python
assert config.count("127.0.0.1:8000") == 1
```

- [x] **Step 2: Add the failing secret and installer test**

Add:

```python
def test_public_caddy_installer_isolates_and_rotates_the_lan_api_key():
    installer = (ROOT / "scripts/install_public_caddy.sh").read_text()
    dropin = (ROOT / "deploy/systemd/caddy-lan-openai.conf").read_text()
    ignored = (ROOT / ".gitignore").read_text().splitlines()

    assert ".env.gateway-lan" in ignored
    assert "EnvironmentFile=/app/devs/TradingNG/.env.gateway-lan" in dropin
    assert "ExecStart=" in dropin
    assert "ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile" in dropin
    assert "--environ" not in dropin
    assert "CODEX_GATEWAY_LAN_API_KEY" in installer
    assert "openssl rand -hex 32" in installer
    assert "--rotate-lan-api-key" in installer
    assert "chmod 0600" in installer
    assert "mv -f --" in installer
    assert "/etc/systemd/system/caddy.service.d/tradingng-lan-openai.conf" in installer
    assert "systemctl daemon-reload" in installer
    assert "systemctl restart caddy" in installer
    assert "systemctl reload caddy" not in installer
    assert ".env.platform" not in dropin
```

In the existing `test_public_caddy_installer_is_domain_and_mode_guarded`, replace:

```python
assert "systemctl reload caddy" in installer
```

with:

```python
assert "systemctl restart caddy" in installer
```

- [x] **Step 3: Run the focused tests and verify RED**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest \
  platform/tests/operations/test_deploy_config.py::test_public_caddy_exposes_only_authenticated_physical_lan_codex_api \
  platform/tests/operations/test_deploy_config.py::test_public_caddy_installer_isolates_and_rotates_the_lan_api_key \
  platform/tests/operations/test_deploy_config.py::test_public_caddy_routes_only_to_loopback_platform_services \
  -q
```

Expected: the new tests fail because the route, drop-in, installer behavior, and ignore entry do not exist. The existing loopback test also fails only after its expectation is updated to require the single new upstream.

### Task 2: Implement the Caddy edge, isolated key, and atomic installer

**Files:**
- Modify: `deploy/caddy/tradingng.caddy`
- Create: `deploy/systemd/caddy-lan-openai.conf`
- Modify: `scripts/install_public_caddy.sh`
- Modify: `.gitignore`
- Test: `platform/tests/operations/test_deploy_config.py`

- [x] **Step 1: Add the ignored live secret path**

Add these adjacent to `.env.platform` in `.gitignore` so both the live secret and its same-filesystem atomic staging file are ignored:

```gitignore
.env.gateway-lan
.env.gateway-lan.*
```

- [x] **Step 2: Add the system Caddy drop-in**

Create `deploy/systemd/caddy-lan-openai.conf`:

```ini
[Service]
EnvironmentFile=/app/devs/TradingNG/.env.gateway-lan
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
```

The empty `ExecStart=` resets the distribution command before replacing it. The replacement deliberately omits `--environ`, which would print the API Key to the journal.

- [x] **Step 3: Add the ordered, fail-closed Caddy route**

Insert this block in `deploy/caddy/tradingng.caddy` after the security headers and before `@apiBearer`:

```caddyfile
	handle /openai/* {
		@lanCodexAuthorized {
			path /openai/v1/models /openai/v1/chat/completions
			remote_ip 192.168.1.0/24
			header Authorization "Bearer {$CODEX_GATEWAY_LAN_API_KEY}"
		}
		handle @lanCodexAuthorized {
			uri strip_prefix /openai
			reverse_proxy 127.0.0.1:8000 {
				header_up -Authorization
			}
		}

		@lanCodexKnownPath {
			path /openai/v1/models /openai/v1/chat/completions
			remote_ip 192.168.1.0/24
		}
		handle @lanCodexKnownPath {
			header Content-Type application/json
			respond `{"error":{"message":"Unauthorized","type":"authentication_error","code":"invalid_api_key"}}` 401
		}

		@lanCodexUnknownPath {
			remote_ip 192.168.1.0/24
		}
		handle @lanCodexUnknownPath {
			header Content-Type application/json
			respond `{"error":{"message":"Not found","type":"invalid_request_error","code":"not_found"}}` 404
		}

		handle {
			header Content-Type application/json
			respond `{"error":{"message":"Forbidden","type":"permission_error","code":"lan_only"}}` 403
		}
	}
```

The enclosing path-specific `handle` keeps the LAN API in the same top-level
handler group and ahead of the generic browser-login handler. Its nested
handlers preserve the authorization, 401, 404, and 403 order. This was verified
against Caddy's adapted JSON after a live probe exposed the global directive
ordering of the earlier `route` form. Do not add this handler to the retired
`deploy/Caddyfile` rollback service.

- [x] **Step 4: Extend installer argument and secret validation state**

In `scripts/install_public_caddy.sh`, add `rotate_lan_api_key=0`, accept the flag, and update usage:

```bash
usage() {
  echo "usage: $0 --mode maintenance|final --confirm-domain ushome.amycat.com [--rotate-lan-api-key]" >&2
}

# In the argument case:
--rotate-lan-api-key) rotate_lan_api_key=1; shift ;;
```

Reject rotation in maintenance mode:

```bash
if ((rotate_lan_api_key)) && [[ "$mode" != "final" ]]; then
  echo "--rotate-lan-api-key requires --mode final" >&2
  exit 2
fi
```

Define the live secret and drop-in paths after the source-config selection:

```bash
lan_env="$project_root/.env.gateway-lan"
dropin_source="$project_root/deploy/systemd/caddy-lan-openai.conf"
dropin_directory="/etc/systemd/system/caddy.service.d"
dropin_path="$dropin_directory/tradingng-lan-openai.conf"
```

- [x] **Step 5: Generate or validate the private key without printing it**

Add a same-filesystem atomic writer near the top of the installer. It keeps the key out of subprocess arguments and makes both installation and rollback replace the live file atomically:

```bash
temporary_secret=""

write_lan_env_line() {
  local line="$1"
  temporary_secret="$(mktemp "$project_root/.env.gateway-lan.XXXXXX")"
  printf '%s\n' "$line" >"$temporary_secret"
  chmod 0600 "$temporary_secret"
  mv -f -- "$temporary_secret" "$lan_env"
  temporary_secret=""
}
```

After the backup state and rollback trap from Step 6 are established, but before installing the Caddy site config, add:

```bash
lan_key_state="not_required"
lan_key=""
if [[ "$mode" == "final" ]]; then
  if ((rotate_lan_api_key)) || [[ ! -f "$lan_env" ]]; then
    lan_key="$(openssl rand -hex 32)"
    write_lan_env_line "CODEX_GATEWAY_LAN_API_KEY=$lan_key"
    secret_changed=1
    if ((rotate_lan_api_key)); then
      lan_key_state="rotated"
    else
      lan_key_state="generated"
    fi
  else
    lan_key_state="reused"
  fi
  [[ "$(stat -c '%a' "$lan_env")" == "600" ]] || {
    echo ".env.gateway-lan must have mode 0600" >&2
    exit 2
  }
  lan_key_line="$(grep -E '^CODEX_GATEWAY_LAN_API_KEY=[0-9a-f]{64}$' "$lan_env" || true)"
  [[ -n "$lan_key_line" && "$(wc -l <"$lan_env")" -eq 1 ]] || {
    echo ".env.gateway-lan is invalid" >&2
    exit 2
  }
  lan_key="${lan_key_line#*=}"
  [[ -f "$dropin_source" ]] || {
    echo "Caddy LAN API drop-in is missing" >&2
    exit 2
  }
fi
```

Immediately after the Caddy restart succeeds, run `unset lan_key lan_key_line` and print only `lan_api_key_state=$lan_key_state`.

- [x] **Step 6: Back up, install, and roll back the drop-in atomically**

Extend the existing backup state with the drop-in and in-memory secret rollback state. Capture the old secret line without printing or copying it to the backup directory:

```bash
dropin_backup="$backup_directory/tradingng-lan-openai.conf.$stamp"
had_dropin=0
dropin_changed=0
had_lan_env=0
old_lan_env_line=""
secret_changed=0
if [[ -f "$dropin_path" ]]; then
  install -m 0600 "$dropin_path" "$dropin_backup"
  had_dropin=1
fi
if [[ "$mode" == "final" && -f "$lan_env" ]]; then
  old_lan_env_line="$(<"$lan_env")"
  had_lan_env=1
fi
```

Install the rollback trap before generating or rotating the key. Extend `rollback()` before restoring Caddy so a failed deployment restores the old key atomically or removes a newly generated key:

```bash
if [[ -n "$temporary_secret" ]]; then
  unlink "$temporary_secret" 2>/dev/null || true
  temporary_secret=""
fi
if ((secret_changed)); then
  if ((had_lan_env)); then
    write_lan_env_line "$old_lan_env_line"
  else
    unlink "$lan_env" 2>/dev/null || true
  fi
fi
if ((dropin_changed)); then
  if ((had_dropin)); then
    install -d -m 0755 "$dropin_directory"
    install -m 0644 "$dropin_backup" "$dropin_path"
  else
    unlink "$dropin_path" 2>/dev/null || true
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
fi
```

Replace both rollback and success `systemctl reload caddy` calls with `systemctl restart caddy`. After installing the site config, manage the drop-in:

```bash
if [[ "$mode" == "final" ]]; then
  install -d -m 0755 "$dropin_directory"
  install -m 0644 "$dropin_source" "$dropin_path"
else
  unlink "$dropin_path" 2>/dev/null || true
fi
dropin_changed=1
systemctl daemon-reload
if [[ "$mode" == "final" ]]; then
  CODEX_GATEWAY_LAN_API_KEY="$lan_key" caddy validate --config "$main_config"
else
  caddy validate --config "$main_config"
fi
systemctl restart caddy
```

Keep the existing config backup and trap semantics. After success, unset
`lan_key`, `lan_key_line`, and `old_lan_env_line`. Do not output the key, the
environment file, or the expanded Caddy configuration.

- [x] **Step 7: Run focused tests and Caddy validation to verify GREEN**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest \
  platform/tests/operations/test_deploy_config.py::test_public_caddy_exposes_only_authenticated_physical_lan_codex_api \
  platform/tests/operations/test_deploy_config.py::test_public_caddy_installer_isolates_and_rotates_the_lan_api_key \
  platform/tests/operations/test_deploy_config.py::test_public_caddy_routes_only_to_loopback_platform_services \
  platform/tests/operations/test_deploy_config.py::test_public_caddy_installer_is_domain_and_mode_guarded \
  platform/tests/operations/test_systemd.py::test_caddy_never_receives_or_dumps_platform_secrets \
  -q
CODEX_GATEWAY_LAN_API_KEY=config-check \
  caddy validate --adapter caddyfile --config deploy/caddy/tradingng.caddy
git check-ignore -q .env.gateway-lan
```

Expected: all focused tests pass, Caddy reports `Valid configuration`, and the ignore check exits 0.

- [x] **Step 8: Commit the implementation**

```bash
cd /app/devs/TradingNG
git add .gitignore deploy/caddy/tradingng.caddy \
  deploy/systemd/caddy-lan-openai.conf scripts/install_public_caddy.sh \
  platform/tests/operations/test_deploy_config.py
git commit -m "feat: protect LAN Codex API at Caddy edge"
```

### Task 3: Document client access and run the complete regression gate

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [x] **Step 1: Document the English operator flow**

After the local Gateway status section in `README.md`, add a `LAN OpenAI-compatible API` subsection that states:

````markdown
### LAN OpenAI-compatible API

The system Caddy exposes only `GET /openai/v1/models` and
`POST /openai/v1/chat/completions` to `192.168.1.0/24`. Gateway itself remains
on loopback. Install the final Caddy configuration as root; the installer
creates a 256-bit key in the ignored, mode-`0600` `.env.gateway-lan` file when
one does not exist:

```bash
sudo /app/devs/TradingNG/scripts/install_public_caddy.sh \
  --mode final --confirm-domain ushome.amycat.com
```

Configure LAN clients with:

```dotenv
OPENAI_BASE_URL=https://ushome.amycat.com/openai/v1
OPENAI_API_KEY=<value securely retrieved from .env.gateway-lan>
```

Retrieve the key only when distributing it through an approved internal secret
channel:

```bash
sudo sed -n 's/^CODEX_GATEWAY_LAN_API_KEY=//p' \
  /app/devs/TradingNG/.env.gateway-lan
```

Rotate the key and immediately invalidate the old value with:

```bash
sudo /app/devs/TradingNG/scripts/install_public_caddy.sh \
  --mode final --confirm-domain ushome.amycat.com --rotate-lan-api-key
```

Public, VPN, Docker, loopback, missing-key, and wrong-key requests are denied.
The key must never be committed, pasted into logs, or used as an OpenAI account
credential.
````

- [x] **Step 2: Document the Chinese operator flow separately**

Add the equivalent `### 局域网 OpenAI 兼容 API` subsection to `README.zh-CN.md`, preserving the same commands, paths, allowed CIDR, exact endpoints, retrieval rule, and rotation behavior. State explicitly that this key protects the local Codex Gateway and is not an OpenAI account key.

- [x] **Step 3: Run documentation and complete repository verification**

Run:

```bash
cd /app/devs/TradingNG
git diff --check
rg -n 'TB[D]|TO[D]O|implement la[t]er|fill in detai[l]s' \
  README.md README.zh-CN.md deploy/caddy/tradingng.caddy \
  deploy/systemd/caddy-lan-openai.conf scripts/install_public_caddy.sh
test -z "$(git status --short TradingAgents)"
bash scripts/verify_platform.sh
```

Expected: placeholder scan and TradingAgents status are silent; Gateway, platform, real MySQL, Web, npm audit, Caddy, identity, and artifact checks all exit 0. Existing explicitly reported migration-database skips remain acceptable.

- [x] **Step 4: Commit documentation**

```bash
cd /app/devs/TradingNG
git add README.md README.zh-CN.md
git commit -m "docs: explain LAN Codex API access"
```

### Task 4: Install and exercise the live physical-LAN boundary

**Files:**
- Create private runtime state only: `.env.gateway-lan` through the root installer.
- Modify system runtime state only: `/etc/caddy/sites-enabled/tradingng.caddy` and `/etc/systemd/system/caddy.service.d/tradingng-lan-openai.conf` through the guarded installer.

- [x] **Step 1: Record pre-deployment state without exposing credentials**

Run service health checks and query MySQL through `Settings()` for total, active, and queued assessment counts. Record the current system Caddy PID and Gateway PID. Do not restart Gateway, API, scheduler, validation, Alpha broker, or workers.

Expected: Gateway and platform services are active; readiness is 200; the assessment counts form the immutable before-state.

- [x] **Step 2: Install the final Caddy configuration and isolated key**

Run:

```bash
cd /app/devs/TradingNG
sudo -n scripts/install_public_caddy.sh \
  --mode final --confirm-domain ushome.amycat.com
```

Expected: output reports only installer mode, backup paths, and `lan_api_key_state=generated` or `reused`. It must not print the key. `systemctl is-active caddy` must report `active`, and `systemctl show caddy -p ExecStart -p EnvironmentFiles` must show the dedicated environment file with no `--environ`.

- [x] **Step 3: Verify source and credential permissions**

Run checks that assert:

```text
.env.gateway-lan mode = 600
.env.gateway-lan is ignored by Git
.env.gateway-lan is not tracked
CODEX_GATEWAY_LAN_API_KEY is exactly 64 lowercase hexadecimal characters
```

Keep the key in a shell variable only for the following checks and never print it.

- [x] **Step 4: Exercise all four Caddy decisions**

Use `curl --resolve ushome.amycat.com:443:192.168.1.31 --interface 192.168.1.31` and assert:

1. correct key + `/openai/v1/models` returns 200 and models `codex`, `codex-fast`, and `codex-slow`;
2. wrong key + known path returns 401 with `invalid_api_key`;
3. correct key + `/openai/internal/status` returns 404 with `not_found`;
4. `--resolve ushome.amycat.com:443:127.0.0.1 --interface 127.0.0.1` + correct key returns 403 with `lan_only`.

Parse and print only status codes and error codes. Do not print headers, the token, response content, or expanded commands.

- [x] **Step 5: Exercise an OpenAI SDK completion**

Use the installed Python OpenAI SDK with:

```python
client = OpenAI(
    base_url="https://ushome.amycat.com/openai/v1",
    api_key=lan_key,
)
models = client.models.list()
completion = client.chat.completions.create(
    model="codex",
    messages=[{"role": "user", "content": "Reply with LAN_API_OK only."}],
)
```

Assert the model IDs contain the three aliases, the response has one non-empty assistant choice, and the finish reason is present. Print only `lan_openai_sdk=passed`; never print the prompt, answer, usage detail, or key.

- [x] **Step 6: Prove no secret or business-state leak**

Load the key in memory and fail if its full value occurs in Caddy or Gateway journal entries since deployment. Re-query total, active, and queued assessment counts; total and statuses must match the before-state except for transitions caused by independently submitted user work. Confirm the previously recorded Gateway PID and all platform service PIDs are unchanged; only system Caddy may have a new PID.

- [x] **Step 7: Verify restart persistence metadata**

Run:

```bash
systemctl is-enabled caddy
systemctl is-active caddy
systemctl cat caddy
```

Expected: Caddy is enabled and active; the installed drop-in loads only `.env.gateway-lan`, replaces `ExecStart`, and contains no `--environ` or `.env.platform`.

### Task 5: Record production acceptance

**Files:**
- Modify: `docs/superpowers/plans/2026-07-31-lan-codex-openai-api.md`

- [x] **Step 1: Check every acceptance statement against fresh evidence**

Mark Tasks 1–4 complete only after focused RED/GREEN evidence, the complete verifier, four live boundary decisions, the SDK completion, secret scan, service persistence, and before/after business-state checks have all passed.

- [x] **Step 2: Run the final repository boundary check**

Run:

```bash
cd /app/devs/TradingNG
git diff --check
test -z "$(git status --short TradingAgents)"
git check-ignore -q .env.gateway-lan
test -z "$(git ls-files .env.gateway-lan var reports)"
git status --short --branch
```

Expected: no formatting error, TradingAgents is clean, the secret is ignored and untracked, and only this acceptance plan is modified.

- [x] **Step 3: Commit acceptance without pushing**

```bash
cd /app/devs/TradingNG
git add docs/superpowers/plans/2026-07-31-lan-codex-openai-api.md
git commit -m "docs: record LAN Codex API acceptance"
```

Do not push unless the user explicitly requests GitHub submission. Never stage `.env.gateway-lan`, `.env.platform`, runtime artifacts, journal output, API responses, or credentials.
