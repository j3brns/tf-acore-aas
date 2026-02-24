#!/usr/bin/env bash
# install-dev-tools.sh — Idempotent pre-agent environment setup
#
# Called automatically by `make task-start` and `make task-resume` before
# the Claude Code agent is launched. Safe to run repeatedly — skips anything
# already present.
#
# Installs:
#   uv            Python toolchain (manages Python 3.12, ruff, mypy, etc.)
#   node 20 LTS   CDK, TypeScript, Claude Code
#   aws CLI v2    AWS operations
#   gh            GitHub CLI (PR creation in finish protocol)
#   cfn-guard     CloudFormation Guard (CDK output policy validation)
#   claude        Claude Code agent
#
# Then syncs project deps:
#   uv sync
#   npm install in infra/cdk and spa
#
# Requirements:
#   - Ubuntu/Debian-based Linux (apt)
#   - sudo access (only needed if tools are missing)
#   - curl, bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS_ARCH="$(uname -m)"  # x86_64 or aarch64

info() { printf "\033[0;34m[install]\033[0m %s\n" "$*"; }
ok()   { printf "\033[0;32m[ok]\033[0m     %s\n" "$*"; }
skip() { printf "\033[0;33m[skip]\033[0m   %s\n" "$*"; }
err()  { printf "\033[0;31m[error]\033[0m  %s\n" "$*" >&2; }

# ---------------------------------------------------------------------------
# 1. Base utilities (apt — only if not present)
# ---------------------------------------------------------------------------
MISSING_PKGS=()
for pkg in curl git make unzip jq ca-certificates gnupg; do
    command -v "$pkg" &>/dev/null || MISSING_PKGS+=("$pkg")
done
if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    info "Installing base utilities: ${MISSING_PKGS[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${MISSING_PKGS[@]}"
else
    skip "base utilities already present"
fi

# ---------------------------------------------------------------------------
# 2. uv
# ---------------------------------------------------------------------------
if command -v uv &>/dev/null; then
    skip "uv $(uv --version)"
else
    info "Installing uv..."
    curl -Ls https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    ok "uv $(uv --version)"
fi

# Ensure uv is on PATH for the rest of this script
export PATH="$HOME/.local/bin:$PATH"

# ---------------------------------------------------------------------------
# 3. Node.js 20 LTS
# ---------------------------------------------------------------------------
if command -v node &>/dev/null && [[ "$(node --version)" == v20* ]]; then
    skip "node $(node --version)"
else
    info "Installing Node.js 20 LTS..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null
    sudo apt-get install -y -qq nodejs
    ok "node $(node --version)"
fi

# ---------------------------------------------------------------------------
# 4. AWS CLI v2
# ---------------------------------------------------------------------------
if command -v aws &>/dev/null; then
    skip "aws $(aws --version 2>&1 | head -1)"
else
    info "Installing AWS CLI v2..."
    if [[ "$OS_ARCH" == "aarch64" ]]; then
        AWS_ZIP="awscli-exe-linux-aarch64.zip"
    else
        AWS_ZIP="awscli-exe-linux-x86_64.zip"
    fi
    curl -fsSL "https://awscli.amazonaws.com/${AWS_ZIP}" -o /tmp/awscliv2.zip
    unzip -q /tmp/awscliv2.zip -d /tmp/awscli-install
    sudo /tmp/awscli-install/aws/install
    rm -rf /tmp/awscliv2.zip /tmp/awscli-install
    ok "aws $(aws --version 2>&1 | head -1)"
fi

# ---------------------------------------------------------------------------
# 5. GitHub CLI
# ---------------------------------------------------------------------------
if command -v gh &>/dev/null; then
    skip "gh $(gh --version | head -1)"
else
    info "Installing GitHub CLI..."
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
    sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq gh
    ok "gh $(gh --version | head -1)"
fi

# ---------------------------------------------------------------------------
# 6. cfn-guard
# ---------------------------------------------------------------------------
if command -v cfn-guard &>/dev/null; then
    skip "cfn-guard $(cfn-guard --version 2>&1)"
else
    info "Installing cfn-guard..."
    CFN_GUARD_VERSION="3.1.1"
    if [[ "$OS_ARCH" == "aarch64" ]]; then
        CFN_ARCH="aarch64-unknown-linux-musl"
    else
        CFN_ARCH="x86_64-unknown-linux-musl"
    fi
    curl -fsSL \
        "https://github.com/aws-cloudformation/cloudformation-guard/releases/download/${CFN_GUARD_VERSION}/cfn-guard-v3-${CFN_ARCH}.tar.gz" \
        | sudo tar -xz -C /usr/local/bin cfn-guard
    sudo chmod +x /usr/local/bin/cfn-guard
    ok "cfn-guard $(cfn-guard --version 2>&1)"
fi

# ---------------------------------------------------------------------------
# 7. Claude Code
# ---------------------------------------------------------------------------
if command -v claude &>/dev/null; then
    skip "claude already installed"
else
    info "Installing Claude Code..."
    sudo npm install -g @anthropic-ai/claude-code --quiet
    ok "claude installed"
fi

# ---------------------------------------------------------------------------
# 8. Project dependencies
# ---------------------------------------------------------------------------
cd "$REPO_ROOT"

info "Syncing Python project dependencies (uv sync)..."
uv sync --quiet
ok "Python deps ready"

info "Syncing CDK Node dependencies..."
npm install --prefix infra/cdk --quiet
ok "infra/cdk deps ready"

info "Syncing SPA Node dependencies..."
npm install --prefix spa --quiet
ok "spa deps ready"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
ok "Environment ready. Proceeding with agent startup."
