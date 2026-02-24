# RUNBOOK-008: Developer Onboarding

## Purpose
Steps for a team lead to follow when a new agent developer joins the team.

## Prerequisites (team lead actions — before the developer's first day)
1. Add developer to Entra group: platform-agent-developers
2. Add developer to GitLab project with Developer role
3. Confirm the developer has: uv, Docker Desktop, AWS CLI v2, Node 20 LTS

## Day 1 Steps (developer follows these)

### 1. Clone and bootstrap
```bash
git clone {repo-url}
cd platform
cp .env.example .env.local
# .env.local requires: VITE_ENTRA_CLIENT_ID, VITE_ENTRA_TENANT_ID, VITE_API_BASE_URL
# Get these values from: docs/development/LOCAL-SETUP.md
make bootstrap
```

### 2. Start local environment and verify
```bash
make dev
make dev-invoke
# Expected: echo agent responds in local environment
```

### 3. Read core documentation (in this order)
- CLAUDE.md — rules and constraints (mandatory)
- docs/ARCHITECTURE.md — understand the system
- docs/development/AGENT-DEVELOPER-GUIDE.md — how to build agents
- docs/development/LOCAL-SETUP.md — local environment details

### 4. First agent task
```bash
# Copy the echo agent as a starting point
cp -r agents/echo-agent agents/my-first-agent
# Edit pyproject.toml: change name, version, owner_team
# Edit handler.py: change the response
make agent-push AGENT=my-first-agent ENV=dev
make agent-invoke AGENT=my-first-agent PROMPT="hello world"
```

## Success Criteria
Developer is considered onboarded when:
- make dev works on their machine
- They can push a modified echo agent to dev in <30 seconds
- They can explain the three invocation modes (sync/streaming/async)
- They know to read the relevant ADR before reversing an architectural decision

## Common Issues
- `.env.local` values missing: check docs/development/LOCAL-SETUP.md for where to find them
- Docker not running: Docker Desktop must be started before `make dev`
- uv not found: install via `curl -Ls https://astral.sh/uv/install.sh | sh`
- GitLab pipeline fails on first push: confirm GitLab role is Developer (not Guest)
