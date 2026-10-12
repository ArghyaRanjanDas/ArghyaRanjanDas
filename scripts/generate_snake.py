#!/usr/bin/env python3
"""Render a contribution snake and decorative particle hits as standalone SVGs."""

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import random
import sys
from urllib.request import Request, urlopen

DT = 0.28 / 1.5
DURATION = 168
STEPS = round(DURATION / DT)
LEVELS = ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
PALETTES = {
    "light": ("#ffffff", "#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39",
              "#30a14e", "#159ca5", "#cf485e", "#b58332"),
    "dark": ("#0d1117", "#161b22", "#0e4429", "#006d32", "#26a641", "#39d353",
             "#39d353", "#5ed5db", "#f07b83", "#e8bf88"),
}


@dataclass
class Calendar:
    columns: int
    days: dict


@dataclass(frozen=True)
class Burst:
    step: int
    center: tuple
    cells: tuple
    color: int
    kind: str
    direction: tuple
    flight: float


@dataclass
class Simulation:
    bodies: list
    changes: dict
    bursts: list
    eaten: list


def parse_calendar(payload):
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("GitHub returned an invalid response or GraphQL errors")
    if "data" in payload:
        try:
            payload = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        except (KeyError, TypeError):
            raise ValueError("GitHub did not return a user's contribution calendar") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("weeks"), list):
        raise ValueError("Expected a contributionCalendar with weeks")
    weeks = payload["weeks"]
    if not 1 <= len(weeks) <= 54:
        raise ValueError("Expected 1–54 contribution weeks")
    days = {}
    dates = set()
    for x, week in enumerate(weeks):
        for day in week["contributionDays"]:
            stamp = date.fromisoformat(day["date"])
            y, count = day["weekday"], day["contributionCount"]
            level = LEVELS.index(day["contributionLevel"])
            if not isinstance(y, int) or not 0 <= y < 7 or not isinstance(count, int) or count < 0:
                raise ValueError("Invalid contribution weekday or count")
            if (stamp.weekday() + 1) % 7 != y or (x, y) in days or stamp in dates:
                raise ValueError("Inconsistent or duplicate contribution date")
            if (count == 0) != (level == 0):
                raise ValueError("Contribution count and level disagree")
            days[x, y] = (stamp.isoformat(), count, level)
            dates.add(stamp)
    if not days:
        raise ValueError("Contribution calendar is empty")
    return Calendar(len(weeks), days)


def load_calendar(user, fixture=None):
    if fixture:
        return parse_calendar(json.loads(Path(fixture).read_text(encoding="utf-8")))
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN is required without --input")
    query = """query($login:String!){user(login:$login){contributionsCollection{
      contributionCalendar{totalContributions weeks{contributionDays{
        date weekday contributionCount contributionLevel
      }}}}}}"""
    request = Request("https://api.github.com/graphql", method="POST",
                      data=json.dumps({"query": query, "variables": {"login": user}}).encode(),
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                               "User-Agent": "contribution-particle-snake"})
    with urlopen(request, timeout=30) as response:
        return parse_calendar(json.load(response))


def neighbors(point, columns):
    x, y = point
    return [(a, b) for a, b in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1))
            if 0 <= a < columns and 0 <= b < 7]


def distance_map(targets, blocked, columns):
    distances = {p: 0 for p in targets if p not in blocked}
    queue = deque(distances)
    while queue:
        point = queue.popleft()
        for candidate in neighbors(point, columns):
            if candidate not in blocked and candidate not in distances:
                distances[candidate] = distances[point] + 1
                queue.append(candidate)
    return distances


def move(body, targets, columns, rng):
    candidates = neighbors(body[0], columns)
    rng.shuffle(candidates)
    choices = []
    for candidate in candidates:
        if candidate in body[:-1]:
            continue
        following = (candidate,) + body[:-1]
        blocked = set(following[1:-1])
        # A route back to the moving tail keeps the fixed-length snake out of pockets.
        if candidate not in distance_map([following[-1]], blocked, columns):
            continue
        distances = distance_map(targets, blocked, columns)
        choices.append((distances.get(candidate, columns * 7 + 1), following))
    if not choices:
        raise ValueError("Snake has no safe next move")
    return min(choices, key=lambda entry: entry[0])[1]


def simulate(calendar, seed):
    rng = random.Random(seed)
    length = min(6, calendar.columns)
    body = tuple((length - 1 - i, 3) for i in range(length))
    bodies = [body]
    changes = {point: [(0, day[2])] for point, day in calendar.days.items()}
    food = {point: 0 for point, day in calendar.days.items() if day[2]}
    bursts, eaten, particle_colors = [], [], []
    next_burst = round(3.64 / DT)  # Keep particle timing independent of snake speed.
    wander = (calendar.columns - 1, 3)
    for step in range(1, STEPS + 1):
        if step == next_burst and step * DT <= DURATION - 10:
            available = [p for p in calendar.days if p not in body]
            nearby = [p for p in available if 5 <= abs(p[0] - body[0][0]) + abs(p[1] - body[0][1]) <= 19]
            if available:
                center = rng.choice(available if rng.random() < .2 else nearby or available)
                sx, sy = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1),
                                     (1, 1), (-1, 1), (1, -1), (-1, -1)))
                kind = rng.choice(("track", "shower", "spark"))
                if kind == "track":
                    offsets = [(sx * i, sy * i) for i in range(-2, rng.randint(2, 5)) if i]
                elif kind == "shower":
                    offsets = [(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1) if x or y]
                    rng.shuffle(offsets)
                    offsets = offsets[:rng.randint(3, 6)]
                else:
                    offsets = [(sx, sy), (2 * sx, 2 * sy), (-sy, sx)][:rng.randint(1, 3)]
                cluster = (center, *(p for dx, dy in offsets
                                     if (p := (center[0] + dx, center[1] + dy)) in available))
                if len(cluster) == 1:
                    cluster += tuple(p for p in neighbors(center, calendar.columns) if p in available)[:1]
                if not particle_colors:
                    particle_colors = list(range(5, 9))
                    rng.shuffle(particle_colors)
                color = particle_colors.pop()
                reach = rng.uniform(55, 110)
                direction = (round(sx * reach, 2), round(sy * reach * .65, 2))
                bursts.append(Burst(step, center, cluster, color, kind, direction, rng.uniform(.45, 1.05)))
                for point in cluster:
                    food[point] = len(bursts)
                    changes[point].append((step, color))
            # Short flurries punctuated by quiet gaps, rather than a steady cadence.
            low, high = rng.choices(((1.3, 2.4), (3, 6), (7, 11)), weights=(3, 5, 2))[0]
            next_burst += rng.randint(math.ceil(low / DT), math.floor(high / DT))
        if food:
            targets = [point for point, generation in food.items() if generation > 0] or list(food)
        else:
            if body[0] == wander:
                wander = (rng.randrange(calendar.columns), rng.randrange(7))
            targets = [wander]
        body = move(body, targets, calendar.columns, rng)
        bodies.append(body)
        if body[0] in food:
            generation = food.pop(body[0])
            eaten.append((step, body[0], generation))
            changes[body[0]].append((step, 0))
    return Simulation(bodies, changes, bursts, eaten)


def number(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


def position(point):
    return 16 + point[0] * 16, 32 + point[1] * 16


def particle_fill(colors, burst, point, age):
    distance = math.hypot(point[0] - burst.center[0], point[1] - burst.center[1])
    peak = .22 + .78 * math.exp(-distance / 1.35)
    intensity = max(.16, peak * (.42 + .58 * math.exp(-age / 1.4)))
    empty, hue = colors[0], colors[burst.color]
    channels = [round(int(empty[i:i + 2], 16) * (1 - intensity) +
                      int(hue[i:i + 2], 16) * intensity) for i in (1, 3, 5)]
    return '#' + ''.join(f'{channel:02x}' for channel in channels)


def cell_frames(changes, point, colors, bursts):
    # Last event wins if activation and eating happen on the same step.
    events = sorted(dict(changes).items())
    frames = []
    for index, (step, level) in enumerate(events):
        if level < 5:
            frames.append((step, colors[level], False))
            continue
        end = events[index + 1][0] if index + 1 < len(events) else STEPS
        last_age = max(0, (end - step) * DT - .012)
        ages = sorted({0, last_age, *(age for age in (.45, 1.8, 5) if age < last_age)})
        for i, age in enumerate(ages):
            fill = particle_fill(colors, bursts[step], point, age)
            frames.append((step + age / DT, fill, i < len(ages) - 1))
    if frames[-1][0] < STEPS:
        frames.append((STEPS, frames[-1][1], False))
    return frames


def render(calendar, simulation, theme, still=False):
    palette = PALETTES[theme]
    width = (calendar.columns + 2) * 16
    colors = palette[1:]
    title = "GitHub contributions with a gold snake"
    desc = ("The initial green grid records actual contributions. Moving tracks, showers and sparks "
            "light decorative green, blue, red and gold clusters for the snake to eat.")
    header = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="192" '
              f'viewBox="0 0 {width} 192" role="img" aria-labelledby="title desc">'
              f'<title id="title">{title}</title><desc id="desc">{desc}</desc>')
    background = f'<rect width="{width}" height="192" rx="8" fill="{palette[0]}"/>'

    def grid(animated):
        cells = []
        for index, (point, day) in enumerate(calendar.days.items()):
            x, y = position(point)
            cls = f' class="cell c{index}"' if animated else ""
            cells.append(f'<rect{cls} x="{x}" y="{y}" width="12" height="12" rx="2" '
                         f'fill="{colors[day[2]]}"><title>{html.escape(day[0])}: '
                         f'{day[1]} contributions</title></rect>')
        return "".join(cells)

    def snake(animated):
        parts = []
        for index in reversed(range(len(simulation.bodies[0]))):
            x, y = position(simulation.bodies[0][index])
            extra = (f' class="segment" style="animation-delay:{number(index * DT)}s"'
                     if animated else "")
            parts.append(f'<rect{extra} x="0" y="0" width="12" height="12" rx="3.5" '
                         f'transform="translate({x} {y})" fill="{"#ffe0ac" if index == 0 else "#e8bf88"}" '
                         f'opacity="{number(1 - index * .085)}"/>')
        return "".join(parts)

    base = background + grid(False) + snake(False)
    if still:
        return header + base + "</svg>\n"
    styles = [f'.film{{animation:scene {number(DURATION)}s linear infinite}}',
              '@keyframes scene{0%,1.2%{opacity:0}1.45%,98.6%{opacity:1}100%{opacity:0}}',
              f'.segment{{animation:travel {number(DURATION)}s linear infinite}}',
              f'.cell{{animation-duration:{number(DURATION)}s;animation-iteration-count:infinite;'
              'animation-timing-function:steps(1,end)}',
              '@media(prefers-reduced-motion:reduce){.film{display:none}}']
    travel = []
    for step, body in enumerate(simulation.bodies):
        x, y = position(body[0])
        travel.append(f'{number(step / STEPS * 100)}%{{transform:translate({x}px,{y}px)}}')
    styles.append('@keyframes travel{' + ''.join(travel) + '}')
    bursts_by_step = {burst.step: burst for burst in simulation.bursts}
    for index, point in enumerate(calendar.days):
        changes = simulation.changes[point]
        if len(changes) > 1:
            frames = ''.join(f'{number(step / STEPS * 100)}%{{fill:{fill};'
                             + ('animation-timing-function:linear' if fade else '') + '}'
                             for step, fill, fade in cell_frames(changes, point, colors, bursts_by_step))
            styles.append(f'.c{index}{{animation-name:f{index}}}@keyframes f{index}{{{frames}}}')
    particles = []
    for index, burst in enumerate(simulation.bursts):
        x, y = position(burst.center)
        x, y = x + 6, y + 6
        dx, dy = burst.direction
        color = colors[burst.color]
        start = (burst.step * DT - burst.flight) / DURATION * 100
        impact = burst.step / STEPS * 100
        end = impact + .7 / DURATION * 100
        styles.append(f'.p{index}{{animation:p{index} {number(DURATION)}s linear infinite;opacity:0}}'
                      f'@keyframes p{index}{{0%,{number(start)}%{{opacity:0;transform:translate({number(x-dx)}px,{number(y-dy)}px)}}'
                      f'{number(start+.04)}%{{opacity:.85}}'
                      f'{number(impact)}%{{opacity:.85;transform:translate({x}px,{y}px)}}'
                      f'{number(impact+.04)}%,100%{{opacity:0;transform:translate({x}px,{y}px)}}}}')
        styles.append(f'.r{index}{{animation:r{index} {number(DURATION)}s linear infinite;opacity:0}}'
                      f'@keyframes r{index}{{0%,{number(impact)}%{{opacity:0;transform:scale(.3)}}'
                      f'{number(impact+.02)}%{{opacity:.65}}'
                      f'{number(end)}%,100%{{opacity:0;transform:scale(2.8)}}}}')
        tail = f'M {number(-dx / 3)} {number(-dy / 3)} L 0 0'
        particles.append(f'<g class="p{index}" stroke="{color}" stroke-linecap="round">'
                         f'<path d="{tail}" fill="none" stroke-width="5" opacity=".15"/>'
                         f'<path d="{tail}" fill="none" stroke-width="1.6"/>'
                         f'<circle r="1.8" fill="{color}" stroke="none"/></g>')
        if burst.kind == "shower":
            flash = ''.join(f'<rect x="{a}" y="{b}" width="2" height="2" rx=".5"/>'
                            for a, b in ((-6, -4), (4, -5), (7, 2), (-2, 7), (-7, 3)))
        elif burst.kind == "track":
            flash = '<path d="M-9-4L-4-2M4 2L9 4M-3 6L-1 2M1-2L3-6" fill="none" stroke-width="1"/>'
        else:
            flash = '<circle r="7" fill="none" stroke-width="1"/><circle cx="-6" cy="-4" r="1"/>'
        particles.append(f'<g transform="translate({x} {y})"><g class="r{index}" '
                         f'fill="{color}" stroke="{color}">{flash}</g></g>')
    return (header + '<style>' + ''.join(styles) + '</style>' + base + '<g class="film">' +
            background + grid(True) + ''.join(particles) + snake(True) + '</g></svg>\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--input", help="GraphQL response or direct contributionCalendar JSON")
    parser.add_argument("--seed", type=int, help="Override the deterministic UTC daily seed")
    parser.add_argument("--out-dir", type=Path, default=Path("assets"))
    args = parser.parse_args(argv)
    calendar = load_calendar(args.user, args.input)
    seed = args.seed
    if seed is None:
        day = datetime.now(timezone.utc).date().isoformat()
        seed = int.from_bytes(hashlib.sha256(f"{args.user}:{day}".encode()).digest()[:8], "big")
    simulation = simulate(calendar, seed)
    outputs = {}
    for theme in PALETTES:
        name = "github-snake" + ("-dark" if theme == "dark" else "")
        outputs[name + ".svg"] = render(calendar, simulation, theme)
        outputs[name + "-still.svg"] = render(calendar, simulation, theme, still=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, svg in outputs.items():
        path = args.out_dir / name
        temporary = path.with_suffix(".svg.tmp")
        temporary.write_text(svg, encoding="utf-8")
        temporary.replace(path)
        print(f"{path}: {len(svg.encode()):,} bytes")
    print(f"{calendar.columns} weeks; {len(simulation.bursts)} particle hits; {number(DURATION)}s loop; seed {seed}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Generation failed: {error}", file=sys.stderr)
        sys.exit(1)
