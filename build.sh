#!/bin/bash
python3 generate_season.py \
  --team teams/greens/u15-2025-26/greens.json \
  --team-2 teams/whites/u15-2025-26/whites.json \
  --fetch-table "$@"
