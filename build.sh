#!/bin/bash
python3 generate_season.py \
  --team teams/greens/greens.json \
  --team-2 teams/whites/whites.json \
  --fetch-table "$@"
