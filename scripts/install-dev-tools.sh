#!/usr/bin/env bash
# install-dev-tools.sh — Idempotent pre-agent environment setup
#
# Called automatically by `make validate-local`, `make task-start`, and
# `make task-resume`. Safe to run repeatedly — skips anything already present.
#
# Hard requirements for `make validate-local`:
#   uv     — ruff, mypy, detect-secrets (installed to ~/.local/bin, no sudo)
#   node   — tsc, cdk synth (any version; v20 preferred)
#
# Soft requirements (warn if missing, do not abort):
#   aws CLI, gh, cfn-guard, claude
#
# In ephemeral environments (containers, Codespaces) where sudo has no
# password, system packages are installed automatically.
# On developer workstations where sudo requires a password, system-level
# installs are skipped and a warning is printed instead.

set -uo pipefail   # -u (unset vars = error), -o pipefail; NOT -e (installs may fail)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS_ARCH="$(uname -m)"  # x86_64 or aarch64
FAILED_TOOLS=()

info()  { printf "[install] %s\n" "$*"; }
ok()    { printf "[ok]      %s\n" "$*"; }
skip()  { printf "[skip]    %s\n" "$*"; }
warn()  { printf "[warn]    %s\n" "$*" >&2; }
fail()  { printf "[fail]    %s\n" "$*" >&2; FAILED_TOOLS+=("$1"); }

# Check once whether passwordless sudo is available
if sudo -n true 2>/dev/null; then
    CAN_SUDO=true
else
    CAN_SUDO=false
    warn "No passwordless sudo — system-level installs skipped (OK on dev workstations)"
fi

# ---------------------------------------------------------------------------
# Helper: apt install a list of packages (no-op if sudo unavailable)
# ---------------------------------------------------------------------------
apt_install() {
    if ! $CAN_SUDO; then return 0; fi
    sudo apt-get update -qq 2>/dev/null
    sudo apt-get install -y -qq "$@" 2>/dev/null || warn "apt install $* failed"
}

# ---------------------------------------------------------------------------
# 1. uv — installs to ~/.local/bin, no sudo needed
# ---------------------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"

if command -v uv &>/dev/null; then
    skip "uv $(uv --version)"
else
    info "Installing uv..."
    if curl -Ls https://astral.sh/uv/install.sh | sh; then
        ok "uv $(uv --version)"
    else
        fail "uv"
        warn "uv is required for validate-local — install manually: curl -Ls https://astral.sh/uv/install.sh | sh"
    fi
fi

# ---------------------------------------------------------------------------
# 2. Node.js (any version acceptable; v20 preferred)
# ---------------------------------------------------------------------------
if command -v node &>/dev/null; then
    NODE_VER="$(node --version)"
    if [[ "$NODE_VER" == v20* ]]; then
        skip "node $NODE_VER"
    else
        skip "node $NODE_VER (v20 preferred but not required)"
    fi
elif $CAN_SUDO; then
    info "Installing Node.js 20 LTS..."
    apt_install ca-certificates gnupg curl
    if curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null \
        && sudo apt-get install -y -qq nodejs; then
        ok "node $(node --version)"
    else
        fail "node"
    fi
else
    # nvm as a no-sudo fallback
    info "Installing Node.js via nvm (no-sudo fallback)..."
    NVM_DIR="$HOME/.nvm"
    if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
        curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash 2>/dev/null || true
    fi
    # shellcheck source=/dev/null
    [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" || true
    if command -v nvm &>/dev/null; then
        nvm install 20 --silent && nvm use 20 --silent
        ok "node $(node --version) via nvm"
    else
        fail "node"
        warn "node is required for validate-local — install Node.js 20 manually"
    fi
fi

# ---------------------------------------------------------------------------
# 3. AWS CLI v2 (soft requirement — needed for ops/deploy, not validate-local)
# ---------------------------------------------------------------------------
if command -v aws &>/dev/null; then
    skip "aws $(aws --version 2>&1 | head -1)"
elif $CAN_SUDO; then
    info "Installing AWS CLI v2..."
    apt_install unzip curl
    AWS_ZIP="awscli-exe-linux-$( [[ "$OS_ARCH" == "aarch64" ]] && echo "aarch64" || echo "x86_64" ).zip"
    if curl -fsSL "https://awscli.amazonaws.com/${AWS_ZIP}" -o /tmp/awscliv2.zip \
        && unzip -q /tmp/awscliv2.zip -d /tmp/awscli-install \
        && sudo /tmp/awscli-install/aws/install; then
        ok "aws $(aws --version 2>&1 | head -1)"
    else
        fail "aws"
    fi
    rm -rf /tmp/awscliv2.zip /tmp/awscli-install 2>/dev/null || true
else
    warn "aws CLI not installed — needed for ops/deploy targets, not validate-local"
fi

# ---------------------------------------------------------------------------
# 4. GitHub CLI (soft requirement — needed for task-finish PR creation)
# ---------------------------------------------------------------------------
if command -v gh &>/dev/null; then
    skip "gh $(gh --version | head -1)"
elif $CAN_SUDO; then
    info "Installing GitHub CLI..."
    apt_install ca-certificates gnupg
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null || true
    sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null || true
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
    if apt_install gh; then
        ok "gh $(gh --version | head -1)"
    else
        fail "gh"
    fi
else
    warn "gh not installed — needed for task-finish PR creation"
fi

# ---------------------------------------------------------------------------
# 5. cfn-guard (soft requirement — needed for infra validation, not validate-local)
# ---------------------------------------------------------------------------
if command -v cfn-guard &>/dev/null; then
    skip "cfn-guard $(cfn-guard --version 2>&1)"
else
    info "Installing cfn-guard..."
    CFN_ARCH="$( [[ "$OS_ARCH" == "aarch64" ]] && echo "aarch64-unknown-linux-musl" || echo "x86_64-unknown-linux-musl" )"
    INSTALL_DIR="$( $CAN_SUDO && echo /usr/local/bin || { mkdir -p "$HOME/.local/bin"; echo "$HOME/.local/bin"; } )"
    # Find latest release tag
    CFN_TAG="$(curl -fsSL https://api.github.com/repos/aws-cloudformation/cloudformation-guard/releases/latest \
        2>/dev/null | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\(.*\)".*/\1/')"
    CFN_TAG="${CFN_TAG:-3.1.1}"
    if curl -fsSL \
        "https://github.com/aws-cloudformation/cloudformation-guard/releases/download/${CFN_TAG}/cfn-guard-v3-${CFN_ARCH}.tar.gz" \
        | tar -xz -C "$INSTALL_DIR" cfn-guard 2>/dev/null \
        && chmod +x "$INSTALL_DIR/cfn-guard"; then
        ok "cfn-guard $(cfn-guard --version 2>&1)"
    else
        fail "cfn-guard"
        warn "cfn-guard install failed — needed for infra validation, not validate-local"
    fi
fi

# ---------------------------------------------------------------------------
# 6. Claude Code (needed for task-start/task-resume to launch agent)
# ---------------------------------------------------------------------------
if command -v claude &>/dev/null; then
    skip "claude already installed"
elif $CAN_SUDO; then
    info "Installing Claude Code (global)..."
    if sudo npm install -g @anthropic-ai/claude-code --quiet 2>/dev/null; then
        ok "claude installed"
    else
        fail "claude"
    fi
else
    info "Installing Claude Code (~/.npm-global)..."
    NPM_GLOBAL="$HOME/.npm-global"
    mkdir -p "$NPM_GLOBAL"
    if npm install --prefix "$NPM_GLOBAL" @anthropic-ai/claude-code --quiet 2>/dev/null; then
        export PATH="$NPM_GLOBAL/bin:$PATH"
        ok "claude installed to ~/.npm-global/bin"
        warn "Add ~/.npm-global/bin to your PATH for claude to be available in new shells"
    else
        fail "claude"
    fi
fi

# ---------------------------------------------------------------------------
# 7. Project deps — HARD REQUIREMENTS (validate-local fails without these)
# ---------------------------------------------------------------------------
cd "$REPO_ROOT"

info "Syncing Python project dependencies (uv sync)..."
if uv sync --quiet; then
    ok "Python deps ready"
else
    echo "[error]   uv sync failed — validate-local will not pass" >&2
    exit 1
fi

info "Syncing CDK Node dependencies (infra/cdk)..."
if npm install --prefix infra/cdk --quiet 2>/dev/null; then
    ok "infra/cdk deps ready"
else
    echo "[error]   npm install in infra/cdk failed — tsc/cdk synth will not pass" >&2
    exit 1
fi

info "Syncing SPA Node dependencies (spa)..."
npm install --prefix spa --quiet 2>/dev/null || warn "spa npm install failed (not needed for validate-local)"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [[ ${#FAILED_TOOLS[@]} -gt 0 ]]; then
    warn "Some tools failed to install: ${FAILED_TOOLS[*]}"
    warn "validate-local will still run — failed tools are only needed for deploy/ops targets"
fi
ok "Environment ready."
