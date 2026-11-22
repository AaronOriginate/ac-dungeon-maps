"""Update the shared styles and navigation in existing map pages."""
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
OUT = TOOLS.parents[2] / "maps"
tpl = (TOOLS / "dunmap_template.html").read_text(encoding="utf-8")
css = "".join((TOOLS / name).read_text(encoding="utf-8") for name in ("theme.css", "map.css"))
tpl = tpl.replace("/*__THEME__*/", css)
start = '<link rel="preconnect"'
end = "</style>"
head = tpl[tpl.index(start):tpl.index(end, tpl.index(start)) + len(end)]
n = 0
for path in sorted(OUT.glob("*.html")):
    if path.name == "index.html":
        continue
    page = path.read_text(encoding="utf-8")
    i = page.index(start)
    j = page.index(end, i) + len(end)
    page = page[:i] + head + page[j:]
    if 'src="nav.js"' not in page:
        page = page.replace("</body></html>", '<script src="nav.js"></script>\n</body></html>')
    path.write_text(page, encoding="utf-8")
    n += 1
print(n, "pages restyled")
