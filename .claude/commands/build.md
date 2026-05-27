Run the default two-team app build, fetching the latest Greens league table from FA Full Time:

```bash
python3 generate_season.py \
  --team teams/greens/greens.json \
  --team-2 teams/whites/whites.json \
  --fetch-table "$@"
```
