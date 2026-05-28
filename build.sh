#!/bin/bash
python3 generate_season.py \
  --team teams/u15-2025-26/greens/greens.json \
  --team-2 teams/u15-2025-26/whites/whites.json \
  --fetch-table "$@"
