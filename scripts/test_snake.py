"""Focused simulation and output checks; no GitHub access or dependencies."""

from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import generate_snake as snake


def calendar_fixture(columns=16, empty=False):
    start = date(2025, 1, 5)  # Sunday, like contributionCalendar's weekday 0.
    weeks = []
    for x in range(columns):
        days = []
        for y in range(7):
            level = 0 if empty else (x * 7 + y) % 5
            days.append({"date": (start + timedelta(days=x * 7 + y)).isoformat(),
                         "weekday": y, "contributionCount": level * 3,
                         "contributionLevel": snake.LEVELS[level]})
        weeks.append({"contributionDays": days})
    return {"weeks": weeks}


class SnakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calendar = snake.parse_calendar(calendar_fixture())
        cls.simulation = snake.simulate(cls.calendar, 1011)

    def test_every_move_is_adjacent_on_board_and_avoids_the_body(self):
        for seed in (1011, 0, 52, 31415):
            simulation = snake.simulate(self.calendar, seed)
            for previous, body in zip(simulation.bodies, simulation.bodies[1:]):
                self.assertEqual(len(body), 6)
                self.assertEqual(len(set(body)), len(body))
                self.assertEqual(body[1:], previous[:-1])
                self.assertIn(body[0], snake.neighbors(previous[0], self.calendar.columns))
                self.assertNotIn(body[0], previous[:-1])
                self.assertTrue(all(0 <= x < self.calendar.columns and 0 <= y < 7 for x, y in body))

    def test_food_changes_only_at_activation_and_actual_head_arrival(self):
        simulation = self.simulation
        active = {point: 0 for point, day in self.calendar.days.items() if day[2]}
        activations = {burst.step: (i, burst) for i, burst in enumerate(simulation.bursts, 1)}
        eaten = {step: (point, generation) for step, point, generation in simulation.eaten}
        self.assertEqual(len(activations), len(simulation.bursts))
        self.assertEqual(len(eaten), len(simulation.eaten))
        replay = {point: [(0, day[2])] for point, day in self.calendar.days.items()}
        for step, body in enumerate(simulation.bodies[1:], 1):
            if step in activations:
                generation, burst = activations[step]
                self.assertIn(burst.center, burst.cells)
                self.assertEqual(len(set(burst.cells)), len(burst.cells))
                for point in burst.cells:
                    self.assertIn(point, self.calendar.days)
                    self.assertNotIn(point, simulation.bodies[step - 1])
                    active[point] = generation
                    replay[point].append((step, burst.color))
            if body[0] in active:
                generation = active.pop(body[0])
                self.assertEqual(eaten[step], (body[0], generation))
                replay[body[0]].append((step, 0))
            else:
                self.assertNotIn(step, eaten)
        self.assertEqual(replay, simulation.changes)
        self.assertTrue(any(generation > 0 for _, _, generation in simulation.eaten))

    def test_bursts_are_spaced_and_irregular(self):
        steps = [burst.step for burst in self.simulation.bursts]
        self.assertTrue(all(0 < step <= snake.STEPS for step in steps))
        intervals = [(b - a) * snake.DT for a, b in zip(steps, steps[1:])]
        self.assertTrue(all(interval >= 1 - 1e-9 for interval in intervals))
        self.assertLess(sum(intervals) / len(intervals), 6)
        self.assertTrue(any(interval < 2.5 for interval in intervals))
        self.assertTrue(any(interval > 7 for interval in intervals))
        self.assertGreater(len(set(intervals)), 3)
        self.assertEqual(snake.DURATION, 168)

    def test_seeded_particles_have_varied_colors_shapes_and_flights(self):
        bursts = self.simulation.bursts
        self.assertEqual({burst.color for burst in bursts}, {5, 6, 7, 8})
        self.assertEqual({burst.kind for burst in bursts}, {'track', 'shower', 'spark'})
        self.assertGreater(len({burst.direction for burst in bursts}), 1)
        self.assertGreater(len({burst.flight for burst in bursts}), 1)
        for burst in bursts:
            self.assertIsInstance(burst.direction, tuple)
            self.assertEqual(len(burst.direction), 2)
            self.assertNotEqual(burst.direction, (0, 0))
            self.assertGreater(burst.flight, 0)
            self.assertLessEqual(burst.flight, burst.step * snake.DT)

    def test_deterministic_self_contained_svg_and_true_stills(self):
        again = snake.simulate(self.calendar, 1011)
        self.assertEqual(again, self.simulation)
        self.assertNotEqual(snake.simulate(self.calendar, 1012).bodies, again.bodies)
        for theme in snake.PALETTES:
            svg = snake.render(self.calendar, again, theme)
            self.assertEqual(svg, snake.render(self.calendar, self.simulation, theme))
            self.assertLess(len(svg.encode()), 250_000)
            self.assertIn('@media(prefers-reduced-motion:reduce){.film{display:none}}', svg)
            ET.fromstring(svg)
            self.assertNotIn('<script', svg)
            self.assertNotIn('href=', svg)
            self.assertNotIn('url(', svg)
            still = snake.render(self.calendar, again, theme, still=True)
            root = ET.fromstring(still)
            self.assertNotIn('animation', still)
            self.assertNotIn('<animate', still)
            ns = '{http://www.w3.org/2000/svg}'
            grid = [node for node in root.findall(ns + 'rect') if node.find(ns + 'title') is not None]
            self.assertEqual(len(grid), len(self.calendar.days))
            self.assertEqual([node.attrib['fill'] for node in grid],
                             [snake.PALETTES[theme][day[2] + 1] for day in self.calendar.days.values()])

    def test_empty_contributions_still_move_and_receive_hits(self):
        calendar = snake.parse_calendar(calendar_fixture(empty=True))
        simulation = snake.simulate(calendar, 52)
        self.assertTrue(simulation.bursts)
        self.assertTrue(simulation.eaten)
        self.assertTrue(all(generation > 0 for _, _, generation in simulation.eaten))

    def test_invalid_api_response_leaves_existing_outputs_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            output = folder / 'github-snake.svg'
            output.write_text('existing SVG')
            fixture = folder / 'input.json'
            for payload in ({'errors': [{'message': 'denied'}]}, {'data': {'user': None}}, {'weeks': []}):
                fixture.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    snake.main(['--user', 'test', '--input', str(fixture), '--out-dir', str(folder)])
                self.assertEqual(output.read_text(), 'existing SVG')


if __name__ == '__main__':
    unittest.main()
