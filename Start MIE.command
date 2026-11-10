#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'MIE mangler Python-miljøet .venv. Se docs/WORKSPACE_APP.md.'
  read 'reply?Trykk Enter for å lukke.'
  exit 1
fi
.venv/bin/python -m app.workspace_launcher
result=$?
if (( result != 0 )); then
  print 'MIE kunne ikke starte. Se feilmeldingen over.'
  read 'reply?Trykk Enter for å lukke.'
fi
exit $result
