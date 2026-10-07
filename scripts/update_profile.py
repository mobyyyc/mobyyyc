#!/usr/bin/env python3
"""Render original profile artwork from publicly visible GitHub data (standard library only)."""

from __future__ import annotations
import argparse
import datetime as dt
import html
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
USERNAME = "mobyyyc"
THEMES = {
    "light": dict(
        ink="#202329",
        muted="#68717d",
        line="#e9edf1",
        accent="#567995",
        glow="#9cb3c7",
        head="#405e78",
        levels=["#eef1f4", "#d5dee7", "#a9bacb", "#7d96ae", "#526f8a"],
    ),
    "dark": dict(
        ink="#e9edf2",
        muted="#9da8b5",
        line="#242d38",
        accent="#b0c8dc",
        glow="#7899b8",
        head="#d5e5f2",
        levels=["#1c2530", "#344354", "#536a81", "#7b96b0", "#b0c8dc"],
    ),
}


def request(url):
    headers = {
        "User-Agent": "mobyyyc-profile/1.0",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token and urllib.parse.urlparse(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=40
    ) as response:
        text = response.read().decode()
    return json.loads(text) if url.startswith("https://api.github.com/") else text


class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = {}
        self.tips = {}
        self.tip_id = None
        self.tip_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td" and "data-date" in attrs:
            self.cells[attrs["id"]] = {
                "date": attrs["data-date"],
                "level": int(attrs["data-level"]),
            }
        if tag == "tool-tip":
            self.tip_id = attrs.get("for")
            self.tip_text = []

    def handle_data(self, data):
        if self.tip_id:
            self.tip_text.append(data)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.tip_id:
            self.tips[self.tip_id] = "".join(self.tip_text).strip()
            self.tip_id = None


def parse_calendar(source, minimum_days=300):
    parser = CalendarParser()
    parser.feed(source)
    days = []
    for cell_id, cell in parser.cells.items():
        match = re.search(
            r"\b(No|[\d,]+) contributions?\b", parser.tips.get(cell_id, "")
        )
        if not match:
            raise ValueError(f"Missing contribution count for {cell['date']}")
        count = 0 if match[1] == "No" else int(match[1].replace(",", ""))
        dt.date.fromisoformat(cell["date"])
        if not 0 <= cell["level"] <= 4 or (count > 0) != (cell["level"] > 0):
            raise ValueError("Invalid calendar cell")
        days.append({**cell, "count": count})
    days.sort(key=lambda day: day["date"])
    dates = [day["date"] for day in days]
    if not minimum_days <= len(days) <= 400 or len(set(dates)) != len(dates):
        raise ValueError("Incomplete or duplicate calendar")
    for left, right in zip(dates, dates[1:]):
        if dt.date.fromisoformat(right) - dt.date.fromisoformat(left) != dt.timedelta(
            days=1
        ):
            raise ValueError("Calendar date gap")
    match = re.search(r"([\d,]+)\s+contributions\s+in the last year", source)
    if match and sum(day["count"] for day in days) != int(match[1].replace(",", "")):
        raise ValueError("Annual total mismatch")
    return days


def next_milestone(stars):
    if stars < 0:
        raise ValueError("Negative stars")
    for target in [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000]:
        if stars < target:
            return target
    return (stars // 10000 + 1) * 10000


def fetch_profile(username=USERNAME):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", username):
        raise ValueError("Invalid username")
    user = request(f"https://api.github.com/users/{username}")
    days = parse_calendar(request(f"https://github.com/users/{username}/contributions"))
    repos = []
    page = 1
    while True:
        batch = request(
            f"https://api.github.com/users/{username}/repos?type=owner&per_page=100&page={page}"
        )
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    stars = sum(
        repo["stargazers_count"]
        for repo in repos
        if not repo["fork"] and repo["owner"]["login"].lower() == username.lower()
    )
    query = urllib.parse.urlencode(
        {
            "q": f"author:{username} type:pr is:public created:{days[0]['date']}..{days[-1]['date']}",
            "per_page": 1,
        }
    )
    prs = request(f"https://api.github.com/search/issues?{query}")
    if prs.get("incomplete_results"):
        raise ValueError("Incomplete PR search")
    return dict(
        username=username,
        as_of=days[-1]["date"],
        followers=user["followers"],
        stars=stars,
        star_target=next_milestone(stars),
        pull_requests=prs["total_count"],
        contributions=sum(day["count"] for day in days),
        days=days,
        sources={
            "calendar": f"https://github.com/users/{username}/contributions",
            "stars": "Current stars on owned, public, non-fork repositories; every repository page.",
            "pull_requests": "Public authored PRs created within the calendar date range, open or closed.",
            "followers": "Current public follower count.",
        },
    )


def svg(width, height, title, desc, theme, content, css=""):
    c = THEMES[theme]
    result = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{html.escape(title)}</title><desc id="desc">{html.escape(desc)}</desc>
<style>text{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;fill:{c['ink']}}}.muted{{fill:{c['muted']}}}{css}</style>
{content}
</svg>"""
    ET.fromstring(result)
    return result


def ribbon_path(index, phase):
    """Smooth cubic curves with matching commands in every animation frame."""
    points = []
    for step in range(13):
        u = step / 12
        envelope = math.sin(math.pi * u) ** 1.1
        x = 380 + u * 516
        y = (
            112
            + (index - 12.5) * 2.4
            + envelope
            * (
                38 * math.sin(u * 2.2 * math.pi + index * 0.055 - phase)
                + 12 * math.sin(u * 4.1 * math.pi - index * 0.07 - phase)
            )
        )
        points.append((x, y))
    path = [f"M{points[0][0]:.2f},{points[0][1]:.2f}"]
    for i, (left, right) in enumerate(zip(points, points[1:])):
        before = points[max(0, i - 1)]
        after = points[min(len(points) - 1, i + 2)]
        control1 = tuple(left[j] + (right[j] - before[j]) / 6 for j in range(2))
        control2 = tuple(right[j] - (after[j] - left[j]) / 6 for j in range(2))
        path.append(
            "C" + " ".join(f"{x:.2f},{y:.2f}" for x, y in [control1, control2, right])
        )
    return " ".join(path)


def hero(theme):
    c = THEMES[theme]
    ribbons, still = [], []
    for i in range(26):
        frames = [ribbon_path(i, frame * math.tau / 24) for frame in range(24)]
        frames.append(frames[0])
        opacity = 0.19 + 0.34 * math.sin(i / 25 * math.pi)
        path = f'<path d="{frames[0]}" fill="none" stroke="url(#silk)" stroke-width="1.05" opacity="{opacity:.3f}">'
        still.append(path + "</path>")
        ribbons.append(
            path
            + f'<animate attributeName="d" values="{";".join(frames)}" dur="8s" calcMode="linear" repeatCount="indefinite"/></path>'
        )
    content = f"""<defs>
<linearGradient id="silk" x1="0" y1="0" x2="1" y2=".3"><stop stop-color="{c['muted']}" stop-opacity=".1"/><stop offset=".35" stop-color="{c['accent']}"/><stop offset=".7" stop-color="{c['glow']}"/><stop offset="1" stop-color="{c['accent']}" stop-opacity=".12"/></linearGradient>
<radialGradient id="atmosphere"><stop stop-color="{c['glow']}" stop-opacity=".10"/><stop offset="1" stop-color="{c['glow']}" stop-opacity="0"/></radialGradient>
<linearGradient id="edge"><stop stop-color="white" stop-opacity="0"/><stop offset=".16" stop-color="white"/><stop offset=".84" stop-color="white"/><stop offset="1" stop-color="white" stop-opacity="0"/></linearGradient>
<mask id="fade"><rect x="370" y="20" width="526" height="188" fill="url(#edge)"/></mask>
</defs>
<ellipse cx="643" cy="112" rx="244" ry="100" fill="url(#atmosphere)"/>
<g mask="url(#fade)"><g class="flow-motion">{''.join(ribbons)}</g><g class="flow-static">{''.join(still)}</g></g>
<text x="0" y="73" font-size="16" letter-spacing="2.1" class="muted">STUDENT &amp; BUILDER / @MOBYYYC</text>
<text x="0" y="147" font-size="72" font-weight="500" letter-spacing="-2.5">Qiyuan Cai</text>
<path d="M1 185 H48" stroke="{c['accent']}" stroke-width="2"/>
"""
    css = ".flow-static{display:none}@media(prefers-reduced-motion:reduce){.flow-motion{display:none}.flow-static{display:inline}}"
    return svg(
        896,
        224,
        "Qiyuan Cai",
        "Computer Science with an AI specialization at the University of Waterloo. Flowing, layered sound ribbons.",
        theme,
        content,
        css,
    )


def stars_svg(data, theme):
    c = THEMES[theme]
    count = data["stars"]
    target = data["star_target"]
    content = f"""<text x="0" y="38" font-size="32" font-weight="500">Stars earned</text>
<text x="896" y="40" text-anchor="end" font-size="36" font-weight="500">{count:,}<tspan class="muted" font-size="30"> / {target:,}</tspan></text>
<rect x="0" y="73" width="896" height="8" rx="4" fill="{c['line']}"/>
<rect class="earned" x="0" y="73" width="{896*count/target:.3f}" height="8" rx="4" fill="{c['accent']}"/>
<text x="0" y="120" font-size="25" class="muted">Next milestone: {target:,} stars</text>"""
    css = ".earned{transform-origin:0 0;animation:grow .9s cubic-bezier(.22,1,.36,1) both}@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}@media(prefers-reduced-motion:reduce){.earned{animation:none}}"
    return svg(
        896,
        148,
        "Stars earned",
        f"{count} stars on owned public non-fork repositories. Next milestone: {target}. Updated {data['as_of']}.",
        theme,
        content,
        css,
    )


def metric_svg(value, label, context, theme):
    content = f"""<text x="0" y="47" font-size="40" font-weight="500" letter-spacing="-1">{value:,}</text>
<text x="0" y="78" font-size="17">{label}</text>
<text x="0" y="104" font-size="13" class="muted">{context}</text>"""
    return svg(280, 124, label, f"{value} {label.lower()}, {context}.", theme, content)


def grid_layout(days):
    start = dt.date.fromisoformat(days[0]["date"])
    start -= dt.timedelta(days=(start.weekday() + 1) % 7)
    cells = []
    for day in days:
        index = (dt.date.fromisoformat(day["date"]) - start).days
        cells.append({**day, "col": index // 7, "row": index % 7})
    columns = max(cell["col"] for cell in cells) + 1
    return cells, columns, min(16, 852 / columns)


def calendar_svg(data, theme):
    c = THEMES[theme]
    cells, columns, step = grid_layout(data["days"])
    parts = []
    previous = None
    for cell in cells:
        month = dt.date.fromisoformat(cell["date"]).strftime("%b")
        x = 26 + cell["col"] * step
        y = 52 + cell["row"] * 16
        if month != previous:
            parts.append(
                f'<text x="{x:.2f}" y="31" font-size="13" class="muted">{month}</text>'
            )
            previous = month
        # Negative delays start a staggered ripple immediately, with a slight diagonal tilt.
        delay = -5.6 + cell["col"] * 0.06 + cell["row"] * 0.04
        parts.append(
            f'<rect class="day" x="{x:.2f}" y="{y}" width="{step-4:.2f}" height="12" rx="2.4" fill="{c["levels"][cell["level"]]}" style="animation-delay:{delay:.3f}s"><title>{cell["date"]}: {cell["count"]} contributions</title></rect>'
        )
    parts.append(
        f'<text x="26" y="189" font-size="18" class="muted">{data["contributions"]:,} contributions in the past year</text>'
    )
    parts.append(
        '<text x="730" y="189" text-anchor="end" font-size="13" class="muted">Less</text>'
    )
    for level in range(5):
        parts.append(
            f'<rect x="{742+level*17}" y="178" width="12" height="12" rx="2.4" fill="{c["levels"][level]}"/>'
        )
    parts.append('<text x="833" y="189" font-size="13" class="muted">More</text>')
    css = ".day{transform-box:fill-box;transform-origin:center;animation:ripple 5.6s ease-in-out infinite}@keyframes ripple{0%,16%,100%{transform:translateY(0) scale(1)}8%{transform:translateY(-4px) scale(1.18)}}@media(prefers-reduced-motion:reduce){.day{animation:none}}"
    return svg(
        896,
        216,
        "A year of building",
        f"{data['contributions']} contributions from {data['days'][0]['date']} through {data['as_of']}. The public GitHub calendar, with a gentle diagonal ripple moving left to right. Blocks lift and enlarge slightly while contribution colors stay constant.",
        theme,
        "".join(parts),
        css,
    )


def picture(name, alt, versions, width=896):
    base = f"https://raw.githubusercontent.com/{USERNAME}/{USERNAME}/main/assets/{name}"
    light = versions[f"assets/{name}-light.svg"]
    dark = versions[f"assets/{name}-dark.svg"]
    return f"""<picture>
  <source media="(prefers-color-scheme: dark)" srcset="{base}-dark.svg?v={dark}">
  <img src="{base}-light.svg?v={light}" width="{width}" alt="{html.escape(alt,quote=True)}">
</picture>"""


def render_readme(data, versions):
    def artwork(name, alt, width=896):
        return picture(name, alt, versions, width)

    u = data["username"]
    cards = "\n".join(
        artwork(name, f"{data[key]:,} {label}, {context}", 264)
        for name, key, label, context in [
            (
                "metric-contributions",
                "contributions",
                "GitHub contributions",
                "past year",
            ),
            ("metric-prs", "pull_requests", "public pull requests", "past year"),
            ("metric-followers", "followers", "followers", "on GitHub"),
        ]
    )
    return f"""<!-- Generated by scripts/update_profile.py. Edit profile copy in render_readme(). -->
{artwork('hero','Qiyuan Cai — student and builder')}

**Computer Science · AI Specialization**<br>
University of Waterloo

I build software around AI, audio, and data — turning experiments into tools people can use.
Currently exploring sound recreation, quantitative research, and AI-assisted workflows.

[Portfolio ↗](https://qiyuancai.vercel.app/) &nbsp; · &nbsp; [All repositories ↗](https://github.com/{u}?tab=repositories)

<br>

### Selected projects

**[NeuroWave ↗](https://github.com/{u}/NeuroWave)**<br>
Exploring sound recreation by learning synthesizer parameters from audio.<br>
<sub>Python · PyTorch · Audio synthesis</sub>

**[Quantrade ↗](https://github.com/{u}/Quantrade)**<br>
A local quantitative research platform with reproducible data pipelines and model evaluation.<br>
<sub>Python · Next.js · PostgreSQL</sub>

**[PM Agent ↗](https://github.com/{u}/pm-agent)**<br>
Turning ideas into structured project plans with AI-assisted refinement.<br>
<sub>TypeScript · Next.js · Gemini</sub>

<br>

### By the numbers

<a href="https://github.com/{u}?tab=repositories&amp;sort=stargazers">
{artwork('stars',f"{data['stars']:,} stars earned on public projects. Next milestone: {data['star_target']:,} stars.")}
</a>

{cards}

<br>

### A year of building

{artwork('contributions',f"{data['contributions']:,} contributions from {data['days'][0]['date']} through {data['as_of']}.")}

<br>

[Pull Shark](https://github.com/{u}?achievement=pull-shark&amp;tab=achievements) &nbsp; · &nbsp; ![Profile views](https://komarev.com/ghpvc/?username={u}&label=PROFILE+VIEWS&color=68717d&style=flat-square)
"""


def build(data):
    if data["contributions"] != sum(day["count"] for day in data["days"]):
        raise ValueError("Snapshot total mismatch")
    output = {
        "data/profile.json": json.dumps(data, indent=2, sort_keys=True) + "\n",
    }
    for theme in THEMES:
        for name, fn in [
            ("hero", lambda: hero(theme)),
            ("stars", lambda: stars_svg(data, theme)),
            ("contributions", lambda: calendar_svg(data, theme)),
        ]:
            output[f"assets/{name}-{theme}.svg"] = fn()
        for name, key, label, context in [
            ("contributions", "contributions", "Contributions", "Past year"),
            ("prs", "pull_requests", "Pull requests", "Public · past year"),
            ("followers", "followers", "Followers", "On GitHub"),
        ]:
            output[f"assets/metric-{name}-{theme}.svg"] = metric_svg(
                data[key], label, context, theme
            )
    versions = {
        name: hashlib.sha256(text.encode()).hexdigest()[:12]
        for name, text in output.items()
        if name.endswith(".svg")
    }
    output["README.md"] = render_readme(data, versions)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    data = (
        json.loads((ROOT / "data/profile.json").read_text())
        if args.offline
        else fetch_profile()
    )
    output = build(data)
    # All fetches, parsing and rendering complete before any last-good artwork is replaced.
    for name, text in output.items():
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(
        f"Generated {len(output)-2} SVGs: {data['contributions']} contributions, {data['stars']} stars, {data['pull_requests']} PRs, {data['followers']} followers. Calendar through {data['as_of']}."
    )


if __name__ == "__main__":
    main()
