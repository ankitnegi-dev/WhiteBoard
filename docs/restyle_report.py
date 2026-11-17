"""Restyle docs/technical-report.html into a cleaner, print-ready report.

Usage:  python restyle_report.py [input.html] [output.html]
Edit TEAM below, then print the output in Chrome (A4, background graphics ON).
"""
import re, sys, html as H
from pathlib import Path

NAME, SUB = "InkLAN", "A LAN-based collaborative whiteboard"
COURSE = "Computer Networks Lab Project 2026"
TEAM = ["Member 1 name", "Member 2 name", "Member 3 name", "Member 4 name"]  # EDIT ME

CSS = r"""
@page { size:A4; margin:22mm 18mm 20mm;
  @bottom-left{content:"InkLAN  |  Technical Report";font:8pt Calibri,Carlito,sans-serif;color:#6b7c85}
  @bottom-right{content:counter(page);font:8pt Calibri,Carlito,sans-serif;color:#6b7c85} }
@page cover { margin:0; @bottom-left{content:none} @bottom-right{content:none} }
:root{--ink:#16262e;--teal:#14535f;--teal2:#1f7a8c;--soft:#eef4f5;--line:#cfdbde;--mute:#5a6d76;--acc:#d9622b}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{font-family:Calibri,Carlito,"Segoe UI",Arial,sans-serif;font-size:10.5pt;line-height:1.5;color:var(--ink);margin:0}
.wrap{max-width:186mm;margin:0 auto}
h1,h2,h3{font-family:Cambria,Caladea,Georgia,serif;color:var(--teal);break-after:avoid;line-height:1.2}
h2{font-size:18pt;margin:1.6em 0 .5em;padding-bottom:.25em;border-bottom:1.5px solid var(--line)}
h2 .n{color:var(--acc);margin-right:.35em}
h3{font-size:13pt;margin:1.3em 0 .4em;color:var(--teal2)}
h3 .n{color:var(--acc);margin-right:.3em}
h2.new{break-before:page;margin-top:0}
p{margin:.55em 0;orphans:3;widows:3}
p.lead{font-weight:700;color:var(--teal);margin-bottom:.2em;break-after:avoid}
ul,ol{margin:.3em 0 .8em;padding-left:1.4em}li{margin:.2em 0}
code{font-family:Consolas,"DejaVu Sans Mono",monospace;font-size:.88em;background:var(--soft);padding:.05em .3em;border-radius:3px;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;margin:.8em 0 1.1em;font-size:9pt;line-height:1.35}
thead{display:table-header-group}
th{background:var(--teal);color:#fff;text-align:left;font-weight:700;padding:5px 7px;border:1px solid var(--teal)}
td{padding:4px 7px;border:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
tr{break-inside:avoid}tbody tr:nth-child(even) td{background:#f6fafa}
table.proto{font-size:8.5pt}table.proto td:first-child{font-family:Consolas,"DejaVu Sans Mono",monospace;font-size:8pt;color:var(--teal)}
figure{margin:1em 0 1.2em;break-inside:avoid;text-align:center}
figure svg{width:100%;max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff}
figcaption,p.figcap{font-size:9pt;color:var(--mute);margin-top:.4em;text-align:left}
.shots{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.shot{break-inside:avoid;border:1px solid var(--line);border-radius:6px;padding:6px;background:#fff}
.shot img{width:100%;height:auto;display:block;border-radius:3px}
.shot .cap{font-size:8.5pt;color:var(--mute);margin-top:4px}
.screenshot-placeholder{background:var(--soft);color:var(--mute);border:1px dashed #9db3ba;border-radius:4px;aspect-ratio:16/10;display:flex;align-items:center;justify-content:center;text-align:center;font-size:8.5pt;padding:8px}
nav.toc{break-after:page}nav.toc h2{border:0;margin-top:0}
nav.toc ol{list-style:none;padding:0;margin:0}nav.toc li{margin:.3em 0;font-size:11pt}
nav.toc li.s{margin-left:1.6em;font-size:10pt;color:var(--mute)}
nav.toc a{text-decoration:none;color:var(--ink)}nav.toc .n{display:inline-block;width:1.8em;color:var(--acc);font-weight:700}nav.toc li.s .n{width:2.4em}
.cover{page:cover;height:296.5mm;overflow:hidden;background:var(--teal);color:#fff;padding:34mm 24mm 24mm;position:relative;break-after:page}
.cover .tag{font-size:10pt;letter-spacing:.18em;text-transform:uppercase;color:#a8d5dc}
.cover h1{font-family:Cambria,Caladea,Georgia,serif;color:#fff;font-size:54pt;margin:.5em 0 .1em;border:0}
.cover .sub{font-size:16pt;color:#d8eef1;max-width:130mm}
.cover .doc{margin-top:26mm;font-size:13pt;font-weight:700}
.cover .team{position:absolute;left:24mm;bottom:24mm;right:24mm;border-top:1px solid #4f8a96;padding-top:6mm;font-size:11pt;color:#e6f4f6}
.cover .team b{display:block;font-size:9pt;letter-spacing:.14em;text-transform:uppercase;color:#a8d5dc;margin-bottom:2mm}
.cover .team span{display:inline-block;margin-right:7mm}
.cover .dot{position:absolute;right:-40mm;top:-40mm;width:130mm;height:130mm;border-radius:50%;background:#1d6a78}
@media screen{body{background:#e9eef0}.wrap,.cover{background:#fff}.wrap{padding:14mm 18mm;margin:12px auto;box-shadow:0 2px 14px #0002}.cover{max-width:210mm;margin:12px auto;background:var(--teal)}}
"""

def slug(t): return re.sub(r"[^a-z0-9]+","-",re.sub(r"<[^>]+>","",t).lower()).strip("-")

def fix_p(m):
    inner = m.group(1)
    if "<svg" in inner: return m.group(0)
    lines = inner.strip("\n").split("\n"); out=[]; text=[]; items=[]; kind=None
    def flush_text():
        nonlocal text
        if text:
            t=" ".join(text).strip()
            cls=' class="figcap"' if re.match(r"Figure \d+:",t) else (' class="lead"' if t.endswith(":") and len(t)<90 else "")
            out.append(f"<p{cls}>{t}</p>"); text=[]
    def flush_items():
        nonlocal items,kind
        if items: out.append(f"<{kind}>"+"".join(f"<li>{i}</li>" for i in items)+f"</{kind}>"); items=[];kind=None
    for ln in lines:
        b=re.match(r"- (.*)",ln); n=re.match(r"\d+\. (.*)",ln)
        if b or n:
            flush_text(); k="ul" if b else "ol"
            if kind and kind!=k: flush_items()
            kind=k; items.append((b or n).group(1))
        elif items: items[-1]+=" "+ln.strip()
        else: text.append(ln.strip())
    flush_text(); flush_items()
    return "\n".join(out)

def main(src, dst):
    s = Path(src).read_text(encoding="utf-8")
    body = s[s.index("<body>")+6:s.index("</body>")]
    body = re.sub(r'<nav class="toc">.*?</nav>',"",body,flags=re.S)
    body = re.sub(r'<h1 id="[^"]*">.*?</h1>',"",body,flags=re.S)
    # screenshots appendix -> grid of cards
    def shots(m):
        cards=[]
        for i,(fn,inner) in enumerate(re.findall(r"<code>(\d\d-[\w-]+\.png)<br>(.*?)</code>",m.group(0),flags=re.S),1):
            cap=re.sub(r"<[^>]+>"," ",inner).split("Screenshot not provided:")[-1]
            capt=re.search(r"</code> - ([^\n<]*)",m.group(0)[m.group(0).find(fn):]); capt=capt.group(1) if capt else fn
            cards.append(f'<div class="shot">{inner}<div class="cap">Screenshot {i}: {H.escape(capt)}</div></div>')
        return '<div class="shots">'+"".join(cards)+"</div>" if cards else m.group(0)
    body = re.sub(r"<p>Screenshot placeholders:.*?</p>",shots,body,flags=re.S)
    body = re.sub(r"<p>(.*?)</p>",fix_p,body,flags=re.S)
    body = re.sub(r"<p>\s*(<svg.*?</svg>)\s*</p>\s*<p class=\"figcap\">(.*?)</p>",r"<figure>\1<figcaption>\2</figcaption></figure>",body,flags=re.S)
    # headings: sequential numbering, TOC
    toc=[]; c2=0; c3=0
    def h(m):
        nonlocal c2,c3
        lvl=m.group(1); t=re.sub(r"^\d+\.\s*","",re.sub(r"<[^>]+>","",m.group(3))).strip()
        if lvl=="2": c2+=1;c3=0;num=str(c2)
        else: c3+=1;num=f"{c2}.{c3}"
        i=f"s{num.replace('.','-')}-{slug(t)}"; toc.append((lvl,num,t,i))
        cls=' class="new"' if lvl=="2" and c2 in (1,3,12,14) else ""
        return f'<h{lvl} id="{i}"{cls}><span class="n">{num}</span>{t}</h{lvl}>'
    body=re.sub(r"<h([23])( id=\"[^\"]*\")?[^>]*>(.*?)</h\1>",h,body,flags=re.S)
    # drop the stray subtitle paragraph (the cover already shows it)
    body=re.sub(r"<p>\s*InkLAN: A LAN-based collaborative whiteboard\.\s*</p>","",body)
    # fallback so the Performance section is never empty (verify values in code!)
    perf=("<p>Performance comes from sending less data and never letting one slow client block the rest.</p><ul>"
      "<li>Stroke points are sent in batches (<code>stroke_points</code>, at most 500 points per message), not one message per mouse move.</li>"
      "<li>Cursor positions are throttled and are not written to the op log.</li>"
      "<li>The message size limit is 1 MB, and an oversize length is rejected before the body is read.</li>"
      "<li>Each client has a bounded send queue and its own writer thread; if the queue overflows the client is moved to resync mode and the room keeps running.</li>"
      "<li>Each client is rate limited (default 60 operations per second, option <code>--rate-limit</code>).</li>"
      "<li>New or reconnecting clients receive only the missing operations (<code>ops_since</code>) unless a snapshot is smaller.</li></ul>")
    body=re.sub(r'(<h2 id="s\d+-performance"[^>]*>.*?</h2>)\s*(?=<h2)',lambda m:m.group(1)+perf,body,flags=re.S)
    tocs="".join(f'<li class="{"s" if l=="3" else ""}"><a href="#{i}"><span class="n">{n}</span>{t}</a></li>' for l,n,t,i in toc)
    body=re.sub(r"<table>(?=\s*<thead>\s*<tr>\s*<th>Type)", '<table class="proto">', body)
    team="".join(f"<span>{H.escape(t)}</span>" for t in TEAM)
    cover=f'<section class="cover"><div class="dot"></div><div class="tag">{COURSE}</div><h1>{NAME}</h1><div class="sub">{SUB}</div><div class="doc">Technical Report and User Guide</div><div class="team"><b>Team</b>{team}</div></section>'
    out=f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{NAME} Technical Report</title><style>{CSS}</style></head><body>{cover}<div class="wrap"><nav class="toc"><h2>Contents</h2><ol>{tocs}</ol></nav>{body}</div></body></html>'
    Path(dst).write_text(out,encoding="utf-8"); print("wrote",dst,len(toc),"headings")

if __name__=="__main__":
    main(*(sys.argv[1:3] or ["technical-report.html","technical-report-styled.html"]))
