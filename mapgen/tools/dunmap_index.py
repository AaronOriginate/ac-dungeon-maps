"""Write maps/index.html and maps/nav.js from maps/_summaries/*.json"""
from pathlib import Path
import html
import json
import re
from urllib.parse import quote

TOOLS = Path(__file__).resolve().parent
OUT = TOOLS.parents[2] / "maps"
ORDER = ["Elemental vaults", "Repugnant Eater task", "Guruk task", "Man-at-Arms task", "Elemental task", "Crystal Minds",
         "Facility Hub 10-15", "Facility Hub 20-25", "Facility Hub 30-45"]
BLURB = {
    "Elemental vaults": "Route from the entrance to each Major stone. Each stone hands in to Zahir in Stonehold for a Writ of Refuge.",
    "Repugnant Eater task": "Shield of Yanshi run plus the 50 Eaters for Xun Yu.",
    "Guruk task": "Colossus (30) and Smasher (40) camps for Brogosh and Kushuk in Kor-Gursha.",
    "Man-at-Arms task": "Viamontian Man-at-Arms (50) for Robert Gutsmasher.",
    "Elemental task": "Elementals (25) for Zahir in Stonehold.",
    "Crystal Minds": "Crystal Minds and Shattered Souls dungeons, in quest order.",
    "Facility Hub 10-15": "Dungeons behind the level 10 and 15 Facility Hub portals. Talk to the Warden by each portal for the quest.",
    "Facility Hub 20-25": "Dungeons behind the level 20 and 25 Facility Hub portals.",
    "Facility Hub 30-45": "Dungeons behind the level 30 to 45 Facility Hub portals.",
}
SHORT = {"Elemental vaults": "Vaults", "Repugnant Eater task": "Eater task", "Man-at-Arms task": "Man-at-Arms", "Facility Hub 10-15": "Hub 10-15",
         "Facility Hub 20-25": "Hub 20-25", "Facility Hub 30-45": "Hub 30-45"}

items = [json.loads(f.read_text(encoding="utf-8")) for f in (OUT / "_summaries").glob("*.json")]
groups = {}
for it in items: groups.setdefault(it.get("group") or "Other", []).append(it)
order = [g for g in ORDER if g in groups] + sorted(g for g in groups if g not in ORDER)
for g in order: groups[g].sort(key=lambda x: x["title"])
e = html.escape
def gid(g): return "g-" + re.sub(r"[^a-z0-9]+", "-", g.lower()).strip("-")
def fm(n): return f"{n:,} m"

def search_text(it, group):
    words = [it["title"], it.get("subtitle", ""), group]
    words.extend(t[0] for t in it.get("targets", []))
    words.extend(r[0] for r in it["routes"])
    return " ".join(words).lower()


# ---- nav.js: shared map switcher for every map page
nav = [dict(slug=it["slug"], title=it["title"], group=g, sub=it.get("subtitle", ""), search=search_text(it, g)) for g in order for it in groups[g]]
js = (TOOLS / "dunmap_nav.js").read_text(encoding="utf-8")
(OUT / "nav.js").write_text("window.MAPNAV = " + json.dumps(dict(maps=nav, groups=order, short=SHORT), separators=(",", ":")) + ";\n" + js, encoding="utf-8")

# ---- index.html
chips = "".join(f'<a class="leather jump" href="#{gid(g)}">{e(SHORT.get(g, g))}<span>{len(groups[g])}</span></a>' for g in order)
rows = []
for g in order:
    cards = []
    for it in groups[g]:
        rs = "".join(f'<li><span>{e(n)}</span><b>{fm(l)}</b></li>' for n, l, lv in it["routes"])
        tg = ", ".join(f"{e(n)} ({c})" for n, lvl, c in it.get("targets", []))
        flags = []
        if it.get("traps"): flags.append('<span class="tag warn">Shaft trap</span>')
        if it.get("locked"): flags.append(f'<span class="tag">{it["locked"]} locked door{"s" if it["locked"] > 1 else ""}</span>')
        q = search_text(it, g)
        cards.append(f'''<a class="card" href="{quote(it["slug"], safe='')}.html" data-q="{e(q)}">
  <h3>{e(it["title"])}</h3>
  <p class="sub">{e(it.get("subtitle", ""))}</p>
  <ul class="routes">{rs}</ul>
  {f'<p class="tg">Targets: {tg}</p>' if tg else ''}
  {('<p class="flags">' + "".join(flags) + '</p>') if flags else ''}
</a>''')
    rows.append(f'<section id="{gid(g)}" data-group><h2 class="sec"><span class="gilt">{e(g)}</span><small>{len(groups[g])} maps</small></h2>'
                f'<p class="blurb">{e(BLURB.get(g, ""))}</p><div class="grid">{"".join(cards)}</div></section>')

CSS = (TOOLS / "theme.css").read_text(encoding="utf-8") + """
html { scroll-padding-top: 150px; }
.wrap { max-width: 1140px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.top { position: sticky; top: 0; z-index: 10; background: linear-gradient(180deg, rgba(43, 31, 21, 0.97), rgba(21, 15, 10, 0.97)); border-bottom: 1px solid var(--ink);
  box-shadow: inset 0 -1px 0 var(--gold-lo), 0 8px 26px rgba(0, 0, 0, 0.55); backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px); }
.top .wrap { display: flex; flex-direction: column; gap: 10px; padding-block: 14px 12px; }
.bar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.brand { margin: 0; flex: 1 1 auto; display: flex; align-items: center; gap: 12px; min-width: 0; }
.brand .gilt { font-size: 24px; letter-spacing: 0.08em; line-height: 1.1; }
.brand svg { flex: none; filter: drop-shadow(0 1px 2px #000); }
.brand small { font-family: var(--body); font-size: 14px; font-style: italic; color: var(--dim); white-space: nowrap; }
.search { position: relative; flex: 0 1 340px; min-width: 0; width: 100%; max-width: 340px; }
.search .field { width: 100%; padding-right: 34px; }
.search kbd { position: absolute; right: 9px; top: 50%; transform: translateY(-50%); }
.jumps { display: flex; gap: 5px; flex-wrap: wrap; }
.jump { display: inline-flex; align-items: center; gap: 7px; padding: 4px 6px 4px 10px; }
.jump span { font-family: var(--body); font-size: 12px; letter-spacing: 0; color: var(--dim); background: rgba(0, 0, 0, 0.35); border-radius: 2px; padding: 0 5px; text-shadow: none; }
.jump.on span { color: #3a280e; background: rgba(255, 255, 255, 0.25); }
main.wrap { padding-block: 26px 60px; display: flex; flex-direction: column; gap: 38px; }
.lead { color: var(--dim); font-style: italic; margin: 0; max-width: 72ch; font-size: 16px; }
.sec { display: flex; align-items: baseline; gap: 12px; margin: 0; font-size: 19px; }
.sec::after { content: ''; flex: 1; height: 1px; align-self: center; background: linear-gradient(90deg, var(--gold-lo), transparent); }
.sec small { font-family: var(--body); font-size: 14px; font-style: italic; letter-spacing: 0; text-transform: none; color: var(--dim); font-weight: 400; order: 2; }
.sec::after { order: 3; }
.blurb { color: var(--dim); margin: 4px 0 14px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(262px, 1fr)); gap: 12px; }
.card { display: flex; flex-direction: column; gap: 6px; padding: 16px 18px 15px; color: inherit; text-decoration: none; min-width: 0;
  background: var(--corners), var(--frame); border: 1px solid var(--ink); border-radius: 3px; box-shadow: var(--edge); transition: box-shadow .15s, transform .15s; }
.card:hover { transform: translateY(-1px); box-shadow: inset 0 0 0 1px var(--gold-hi), inset 0 0 0 2px rgba(0, 0, 0, 0.65), inset 0 0 18px rgba(244, 220, 156, 0.14), 0 10px 28px rgba(0, 0, 0, 0.6); }
.card h3 { font-family: var(--display); font-weight: 700; font-size: 16px; letter-spacing: 0.05em; color: var(--gold-hi); margin: 0; }
.card .sub { color: var(--dim); margin: 0; font-size: 14px; font-style: italic; line-height: 1.3; }
.routes { list-style: none; margin: 4px 0 0; padding: 6px 0 0; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 2px; }
.routes li { display: flex; justify-content: space-between; gap: 12px; font-size: 14.5px; }
.routes b { font-weight: 600; color: var(--gold-hi); font-variant-numeric: lining-nums tabular-nums; white-space: nowrap; }
.tg { margin: 0; font-size: 14px; }
.flags { margin: 2px 0 0; display: flex; gap: 5px; flex-wrap: wrap; }
.tag { font-family: var(--display); font-size: 9.5px; letter-spacing: 0.06em; border-radius: 2px; padding: 1px 6px; border: 1px solid var(--line); color: var(--dim); }
.tag.warn { color: #ffb4a3; border-color: rgba(255, 90, 61, 0.6); }
.empty { color: var(--dim); font-style: italic; padding: 24px 0; }
@media (max-width: 640px) { .search { max-width: none; flex-basis: 100%; } .brand small { display: none; } html { scroll-padding-top: 200px; } }
@media (prefers-reduced-motion: reduce) { .card { transition: none; } .card:hover { transform: none; } }
"""

page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>AC Dungeon Maps</title>
<meta name="description" content="3D dungeon maps for Asheron's Call, built from the game files, with routes to quest goals and kill task sweeps.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M8 1l5 7-5 7-5-7z' fill='%23e0b25a'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Crimson+Pro:ital,wght@0,400;0,600;1,400&display=swap">
<style>{CSS}</style></head><body>
<header class="top"><div class="wrap">
  <div class="bar">
    <h1 class="brand"><svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1l5 7-5 7-5-7z" fill="#e6bd6c"/></svg><span class="gilt">AC Dungeon Maps</span><small>{len(items)} maps</small></h1>
    <label class="search"><input class="field" id="q" type="search" placeholder="Search maps, monsters, items" aria-label="Search maps" autocomplete="off"><kbd>/</kbd></label>
  </div>
  <nav class="jumps" aria-label="Map groups">{chips}</nav>
</div></header>
<main class="wrap">
<p class="lead">3D maps built from the game files, with routes to quest goals and sweeps for kill tasks. Drag to rotate a map. On any map, press M to jump to another, or [ and ] to step through them.</p>
{"".join(rows)}
<p class="empty" id="empty" hidden>No maps match that search.</p>
</main>
<script>
(() => {{
  const q = document.getElementById('q'), secs = [...document.querySelectorAll('section[data-group]')], empty = document.getElementById('empty');
  const jumps = [...document.querySelectorAll('.jump')];
  function filter() {{
    const t = q.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
    let shown = 0;
    secs.forEach(s => {{
      let n = 0;
      s.querySelectorAll('.card').forEach(c => {{ const ok = t.every(w => c.dataset.q.includes(w)); c.hidden = !ok; n += ok; }});
      s.hidden = !n; shown += n;
      const j = jumps.find(a => a.getAttribute('href') === '#' + s.id); if (j) {{ j.hidden = !n; j.querySelector('span').textContent = n; }}
    }});
    empty.hidden = shown > 0;
  }}
  q.addEventListener('input', filter);
  addEventListener('keydown', ev => {{
    if (ev.key === '/' && document.activeElement !== q) {{ ev.preventDefault(); q.focus(); q.select(); }}
    else if (ev.key === 'Escape' && document.activeElement === q) {{ q.value = ''; filter(); q.blur(); }}
  }});
  const io = new IntersectionObserver(es => es.forEach(en => {{
    if (en.isIntersecting) jumps.forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#' + en.target.id));
  }}), {{ rootMargin: '-170px 0px -65% 0px' }});
  secs.forEach(s => io.observe(s));
}})();
</script>
</body></html>'''
(OUT / "index.html").write_text(page, encoding="utf-8")
print(len(items), "maps indexed")
