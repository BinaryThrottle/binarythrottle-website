# BinaryThrottle website

Static site, single `index.html`. No build step. Auto-deploys to Vercel on every push to `main`.

## Local preview

```bash
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy

Push to `main` → Vercel picks it up via the GitHub integration.

```bash
git add .
git commit -m "Update site"
git push origin main
```
