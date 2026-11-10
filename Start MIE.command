#!/bin/zsh
# The Desktop checkout is preserved history. Start the main-based workspace.
mie_workspace="$HOME/MIE_SECURE_WORKTREE_2026-11-03"
if [[ ! -f "$mie_workspace/AGENTS.md" || ! -f "$mie_workspace/app/workspace.py" || ! -f "$mie_workspace/Start MIE.command" ]]; then
  print -u2 'Den samlede MIE-arbeidsflaten mangler. Se docs/MIE_CONSOLIDATION_2026-11-10.md.'
  exit 1
fi
exec /bin/zsh "$mie_workspace/Start MIE.command" "$@"
