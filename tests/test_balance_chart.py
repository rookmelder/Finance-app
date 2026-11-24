"""Tests for SVG line chart of the bank balance."""

import datetime
import re
import unittest

from src.balance_chart import _euro, chart_svg


class TestBalanceChart(unittest.TestCase):
    def test_euro_formatting_cases(self):
        """Must cover euro formatting cases: 0, 5, 123456, -250, 99999999."""
        self.assertEqual(_euro(0), "€0.00")
        self.assertEqual(_euro(5), "€0.05")
        self.assertEqual(_euro(123456), "€1,234.56")
        self.assertEqual(_euro(-250), "-€2.50")
        self.assertEqual(_euro(99999999), "€999,999.99")

        # Also test cents_digits=0
        self.assertEqual(_euro(0, 0), "€0")
        self.assertEqual(_euro(123456, 0), "€1,235")
        self.assertEqual(_euro(-250, 0), "-€3")
        self.assertEqual(_euro(-25000, 0), "-€250")

    def test_empty_points(self):
        """Returns empty string when points is empty."""
        today = datetime.date(2026, 11, 24)
        self.assertEqual(chart_svg([], today), "")

    def test_single_point(self):
        """Single point renders properly without division by zero."""
        today = datetime.date(2026, 11, 24)
        points = [
            {
                "date": "2026-11-24",
                "amount": 10000,
                "delta": 10000,
                "kind": "income",
                "label": "Initial",
            }
        ]
        svg = chart_svg(points, today)
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(svg.endswith("</svg>"))
        self.assertIn('class="balance-chart"', svg)
        self.assertIn("<title>", svg)
        # Dot count is 1
        dots = re.findall(r'<circle class="dot"', svg)
        self.assertEqual(len(dots), 1)
        # Path for 1 point has 1 M and 1 L command (flat to right edge)
        match = re.search(r'<path class="line" d="([^"]+)"', svg)
        self.assertIsNotNone(match)
        d = match.group(1)
        self.assertEqual(d.count("M"), 1)
        self.assertEqual(d.count("L"), 1)

    def test_two_points_same_day(self):
        """Two points on the same day widen to 1-day span and render vertical jump."""
        today = datetime.date(2026, 11, 24)
        points = [
            {
                "date": "2026-11-24",
                "amount": 1000,
                "delta": 1000,
                "kind": "income",
                "label": "Morning",
            },
            {
                "date": "2026-11-24",
                "amount": 3000,
                "delta": 2000,
                "kind": "income",
                "label": "Afternoon",
            },
        ]
        svg = chart_svg(points, today)
        self.assertIn("<svg", svg)
        dots = re.findall(r'<circle class="dot"', svg)
        self.assertEqual(len(dots), 2)
        match = re.search(r'<path class="line" d="([^"]+)"', svg)
        self.assertIsNotNone(match)
        d = match.group(1)
        self.assertEqual(d.count("M"), 1)
        # 1 horizontal step + 1 vertical step + 1 flat to right edge = 3 L commands
        self.assertEqual(d.count("L"), 3)

    def test_steps_horizontal_then_vertical(self):
        """Steps in path d follow horizontal-then-vertical pattern and command counts."""
        today = datetime.date(2026, 11, 24)
        points = [
            {
                "date": "2026-11-20",
                "amount": 10000,
                "delta": 10000,
                "kind": "income",
                "label": "Start",
            },
            {
                "date": "2026-11-22",
                "amount": 15000,
                "delta": 5000,
                "kind": "income",
                "label": "Bonus",
            },
            {
                "date": "2026-11-24",
                "amount": 8000,
                "delta": -7000,
                "kind": "expense",
                "label": "Rent",
            },
        ]
        svg = chart_svg(points, today)
        match = re.search(r'<path class="line" d="([^"]+)"', svg)
        self.assertIsNotNone(match)
        d = match.group(1)

        # 3 points: 1 M command, 2*(3-1) + 1 = 5 L commands
        self.assertEqual(d.count("M"), 1)
        self.assertEqual(d.count("L"), 5)

        # Pattern: M x0 y0 L x1 y0 L x1 y1 L x2 y1 L x2 y2 L x_end y2
        tokens = d.split()
        commands = []
        i = 0
        while i < len(tokens):
            cmd = tokens[i]
            x = float(tokens[i + 1])
            y = float(tokens[i + 2])
            commands.append((cmd, x, y))
            i += 3

        self.assertEqual(len(commands), 6)
        self.assertEqual(commands[0][0], "M")
        for k in range(1, 6):
            self.assertEqual(commands[k][0], "L")

        # Step 1: horizontal (same y0 as M, new x1), then vertical (same x1, new y1)
        self.assertEqual(commands[1][2], commands[0][2])  # y1_horiz == y0
        self.assertEqual(commands[2][1], commands[1][1])  # x1_vert == x1_horiz

        # Step 2: horizontal (same y1, new x2), then vertical (same x2, new y2)
        self.assertEqual(commands[3][2], commands[2][2])  # y2_horiz == y1
        self.assertEqual(commands[4][1], commands[3][1])  # x2_vert == x2_horiz

        # Final extension: flat to right edge (same y2, right edge x)
        self.assertEqual(commands[5][2], commands[4][2])
        self.assertGreaterEqual(commands[5][1], commands[4][1])

    def test_negative_balances_zero_line_and_negative_labels(self):
        """Negative balances display the zero line and negative labels."""
        today = datetime.date(2026, 11, 24)
        # Spanning positive and negative
        points = [
            {
                "date": "2026-11-20",
                "amount": 5000,
                "delta": 5000,
                "kind": "income",
                "label": "Start",
            },
            {
                "date": "2026-11-22",
                "amount": -3000,
                "delta": -8000,
                "kind": "expense",
                "label": "Overdraft",
            },
        ]
        svg = chart_svg(points, today)
        self.assertIn('class="zero"', svg)
        self.assertIn("-€", svg)

        # All negative balances
        points_all_neg = [
            {
                "date": "2026-11-20",
                "amount": -5000,
                "delta": -5000,
                "kind": "expense",
                "label": "Debt",
            },
            {
                "date": "2026-11-22",
                "amount": -2000,
                "delta": 3000,
                "kind": "income",
                "label": "Payback",
            },
        ]
        svg_neg = chart_svg(points_all_neg, today)
        self.assertIn('class="zero"', svg_neg)
        self.assertIn("-€", svg_neg)

        # All positive balances should not show zero line
        points_all_pos = [
            {
                "date": "2026-11-20",
                "amount": 50000,
                "delta": 50000,
                "kind": "income",
                "label": "Salary",
            },
            {
                "date": "2026-11-22",
                "amount": 60000,
                "delta": 10000,
                "kind": "income",
                "label": "Bonus",
            },
        ]
        svg_pos = chart_svg(points_all_pos, today)
        self.assertNotIn('class="zero"', svg_pos)

    def test_label_escaping(self):
        """Label with special XML characters must be escaped."""
        today = datetime.date(2026, 11, 24)
        raw_label = 'a "b" <c> & d'
        points = [
            {
                "date": "2026-11-24",
                "amount": 1000,
                "delta": 1000,
                "kind": "income",
                "label": raw_label,
            }
        ]
        svg = chart_svg(points, today)
        self.assertNotIn("<c>", svg)
        self.assertIn("&lt;c&gt;", svg)
        self.assertIn("&quot;b&quot;", svg)
        self.assertIn("&amp;", svg)

    def test_dot_count_cap_at_120(self):
        """Dots are capped at last 120 points while path uses all points."""
        today = datetime.date(2026, 11, 24)
        points = []
        base_date = datetime.date(2026, 1, 1)
        for i in range(150):
            d = base_date + datetime.timedelta(days=i)
            points.append(
                {
                    "date": d.isoformat(),
                    "amount": 1000 + i * 10,
                    "delta": 10,
                    "kind": "income",
                    "label": f"Pt {i}",
                }
            )
        svg = chart_svg(points, today)
        dots = re.findall(r'<circle class="dot"', svg)
        self.assertEqual(len(dots), 120)

        # Path still uses all 150 points: 1 M, 2*(150-1) + 1 = 299 L commands
        match = re.search(r'<path class="line" d="([^"]+)"', svg)
        self.assertIsNotNone(match)
        d = match.group(1)
        self.assertEqual(d.count("M"), 1)
        self.assertEqual(d.count("L"), 299)

    def test_security_no_scripts_onclick_http(self):
        """No <script, onclick, or http in the output."""
        today = datetime.date(2026, 11, 24)
        points = [
            {
                "date": "2026-11-24",
                "amount": 5000,
                "delta": 5000,
                "kind": "income",
                "label": "Salary payment",
            }
        ]
        svg = chart_svg(points, today)
        self.assertNotIn("<script", svg.lower())
        self.assertNotIn("onclick", svg.lower())
        self.assertNotIn("http", svg.lower())

    def test_aria_label_contains_last_amount(self):
        """Aria-label contains the last amount formatted."""
        today = datetime.date(2026, 11, 24)
        points = [
            {
                "date": "2026-11-20",
                "amount": 1000,
                "delta": 1000,
                "kind": "income",
                "label": "First",
            },
            {
                "date": "2026-11-24",
                "amount": 123456,
                "delta": 122456,
                "kind": "income",
                "label": "Second",
            },
        ]
        svg = chart_svg(points, today)
        self.assertIn('aria-label="Bank balance over time, now €1,234.56"', svg)

    def test_set_jump_and_long_span_365_days(self):
        """A 'set' jump and a 365-day span render without crashing."""
        today = datetime.date(2026, 12, 31)
        points = [
            {
                "date": "2026-01-01",
                "amount": 10000,
                "delta": 10000,
                "kind": "set",
                "label": "Initial balance",
            },
            {
                "date": "2026-06-15",
                "amount": 80000,
                "delta": 70000,
                "kind": "income",
                "label": "Mid-year salary",
            },
            {
                "date": "2026-12-31",
                "amount": 25000,
                "delta": -55000,
                "kind": "set",
                "label": "Year-end reconciliation",
            },
        ]
        svg = chart_svg(points, today)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('class="balance-chart"', svg)
        dots = re.findall(r'<circle class="dot"', svg)
        self.assertEqual(len(dots), 3)

    def test_y_labels_distinct(self):
        """Y labels must be distinct and count between 3 and 5."""
        today = datetime.date(2026, 11, 24)

        # Test small range (<= 20 000 cents): cents included
        points_small = [
            {
                "date": "2026-11-20",
                "amount": 1000,
                "delta": 1000,
                "kind": "income",
                "label": "A",
            },
            {
                "date": "2026-11-24",
                "amount": 1800,
                "delta": 800,
                "kind": "income",
                "label": "B",
            },
        ]
        svg_small = chart_svg(points_small, today)
        y_labels_small = re.findall(
            r'<text class="label y-label"[^>]*>([^<]+)</text>', svg_small
        )
        self.assertGreaterEqual(len(y_labels_small), 3)
        self.assertLessEqual(len(y_labels_small), 5)
        self.assertEqual(len(y_labels_small), len(set(y_labels_small)))

        # Test large range (> 20 000 cents): cents omitted
        points_large = [
            {
                "date": "2026-11-20",
                "amount": 10000,
                "delta": 10000,
                "kind": "income",
                "label": "A",
            },
            {
                "date": "2026-11-24",
                "amount": 350000,
                "delta": 340000,
                "kind": "income",
                "label": "B",
            },
        ]
        svg_large = chart_svg(points_large, today)
        y_labels_large = re.findall(
            r'<text class="label y-label"[^>]*>([^<]+)</text>', svg_large
        )
        self.assertGreaterEqual(len(y_labels_large), 3)
        self.assertLessEqual(len(y_labels_large), 5)
        self.assertEqual(len(y_labels_large), len(set(y_labels_large)))


if __name__ == "__main__":
    unittest.main()
