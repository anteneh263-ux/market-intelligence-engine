#!/bin/zsh
cd -- "${0:A:h}" || exit 1
mie_python="$PWD/.venv/bin/python"
if [[ ! -x "$mie_python" ]]; then
  mie_python="$HOME/Desktop/market-intelligence-engine/.venv/bin/python"
fi
if [[ ! -x "$mie_python" ]]; then
  print -u2 'MIE mangler Python-miljøet. Se docs/WORKSPACE_APP.md.'
  exit 1
fi
exec "$mie_python" -B -m app.workspace_launcher "$@"
