Commit and push all built HTML files to GitHub Pages (triggers auto-deploy to https://shurdsfield.github.io/stjohns):

```bash
git add index.html u13/index.html
git status
git diff --cached --stat
```

Then commit with a descriptive message and push:

```bash
git commit -m "Rebuild: <brief description of what changed>"
git push origin main
```
