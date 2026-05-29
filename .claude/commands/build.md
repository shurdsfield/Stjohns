Run the default two-team app build, fetching the latest Greens league table from FA Full Time:

```bash
python3 generate_season.py \
  --team teams/u15-2025-26/greens/greens.json \
  --team-2 teams/u15-2025-26/whites/whites.json \
  --fetch-table "$@"
```
