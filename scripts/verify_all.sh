#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for python_path in \
  path-track-benchmark/.venv/bin/python \
  ibtracs-agency-disagreement/.venv/bin/python \
  markov/.venv/bin/python
do
  if [[ ! -x "$python_path" ]]; then
    printf 'Missing project interpreter: %s\n' "$python_path" >&2
    exit 2
  fi
done

printf '\n[A] path benchmark tests\n'
PYTHONPATH=path-track-benchmark/src \
  path-track-benchmark/.venv/bin/python -m unittest discover \
  -s path-track-benchmark/tests -v

printf '\n[B] agency disagreement and landfall tests\n'
PYTHONPATH=ibtracs-agency-disagreement/src \
  ibtracs-agency-disagreement/.venv/bin/python -m unittest discover \
  -s ibtracs-agency-disagreement/tests -v

printf '\n[C/Markov] structure, event, solver, and sensitivity tests\n'
PYTHONPATH=markov/src \
  markov/.venv/bin/python -m unittest discover -s markov/tests -v

printf '\n[Synthesis] cross-project evidence and artifact integrity\n'
markov/.venv/bin/python \
  scripts/build_intensity_predictability_ceilings.py --check

markov/.venv/bin/python -m compileall -q \
  scripts \
  path-track-benchmark/src path-track-benchmark/scripts path-track-benchmark/tests \
  ibtracs-agency-disagreement/src ibtracs-agency-disagreement/scripts \
  ibtracs-agency-disagreement/tests \
  markov/src markov/scripts markov/tests

git diff --check
printf '\nAll project tests, synthesis checks, compileall, and git diff checks passed.\n'
