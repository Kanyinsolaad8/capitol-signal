"""Build the app from data/data.json and state/portfolio.json.

Writes:
  site/            the installable web app published to GitHub Pages (index.html, manifest, icons)
  app/capitol-signal.html   the same page without <head>, for previewing as a claude.ai artifact
"""
import json, pathlib, shutil

root = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((root / "data" / "data.json").read_text())
for t in data["trades"]:  # the page only shows scored bill links per trade; keeps it light on phones
    t["bills"] = [b for b in t["bills"] if b["strength"] >= 2][:2]
pfile = root / "state" / "portfolio.json"
portfolio = json.loads(pfile.read_text()) if pfile.exists() else {}


def blob(x):
    return json.dumps(x, separators=(",", ":")).replace("</", "<\\/")


body = (root / "app" / "template.html").read_text().replace("__DATA__", blob(data)).replace("__PORTFOLIO__", blob(portfolio))
(root / "app" / "capitol-signal.html").write_text(body)

HEAD = """<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#2f4a8a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Capitol Signal">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="robots" content="noindex, nofollow">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
img{max-width:100%}[hidden]{display:none!important}</style>
</head>
<body>
"""
site = root / "site"
shutil.rmtree(site, ignore_errors=True)
site.mkdir()
(site / "index.html").write_text(HEAD + body + "\n</body>\n</html>\n")
for f in (root / "app" / "static").iterdir():
    shutil.copy(f, site / f.name)
(site / ".nojekyll").write_text("")
print(f"site/index.html: {len(body) / 1e6:.2f} MB")
