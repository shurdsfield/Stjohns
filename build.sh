#!/bin/bash
set -e

FETCH_STATS=0
FETCH_TABLE=0
TEAMS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fetch-stats) FETCH_STATS=1; shift ;;
    --fetch-table) FETCH_TABLE=1; shift ;;
    u13|u13s)      TEAMS+=(u13s); shift ;;
    u15|greens)    TEAMS+=(greens); shift ;;
    u15whites|whites) TEAMS+=(whites); shift ;;
    all)           TEAMS=(greens whites u13s); shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# Default: build all teams
if [[ ${#TEAMS[@]} -eq 0 ]]; then
  TEAMS=(greens whites u13s)
fi

EXTRA_FLAGS=()
[[ $FETCH_TABLE -eq 1 ]] && EXTRA_FLAGS+=(--fetch-table)
[[ $FETCH_STATS -eq 1 ]] && EXTRA_FLAGS+=(--fetch-stats)

for TEAM in "${TEAMS[@]}"; do
  case "$TEAM" in
    greens)
      echo "── Building U15 Greens ──"
      python3 generate_season.py \
        --team teams/u15-2025-26/greens/greens.json \
        "${EXTRA_FLAGS[@]}"
      ;;
    whites)
      echo "── Building U15 Whites ──"
      python3 generate_season.py \
        --team teams/u15-2025-26/whites/whites.json \
        "${EXTRA_FLAGS[@]}"
      ;;
    u13s)
      echo "── Building U13s ──"
      python3 generate_season.py \
        --config teams/u13-2025-26/u13s/season_config.json \
        --team   teams/u13-2025-26/u13s/u13s.json \
        "${EXTRA_FLAGS[@]}"
      ;;
  esac
done
