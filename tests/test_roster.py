import copy
import json
import os
import unittest

from src.roster import NotARoster, RosterError, parse_roster


class TestRosterParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_path = os.path.join(
            os.path.dirname(__file__), "fixtures", "roster_tokens.json"
        )
        with open(fixture_path, "r", encoding="utf-8") as f:
            cls.tokens = json.load(f)

    def test_fixture_test_person(self):
        """Test Person gives expected month, 8 days, total_minutes 3600, revised date, and legend."""
        res = parse_roster(self.tokens, "Test Person")

        self.assertEqual(res["month"], "2026-12")
        self.assertEqual(res["total_minutes"], 3600)
        self.assertEqual(res["revised"], "2026-11-21")

        # Legend checks for D, V, L
        legend = res["legend"]
        self.assertIn("D", legend)
        self.assertEqual(legend["D"], {"start": 510, "end": 990, "minutes": 450})
        self.assertIn("V", legend)
        self.assertEqual(legend["V"], {"start": 450, "end": 930, "minutes": 450})
        self.assertIn("L", legend)
        self.assertEqual(legend["L"], {"start": 810, "end": 1290, "minutes": 450})

        # Expected 8 days
        expected_days = [
            {"date": "2026-12-06", "minutes": 450, "code": "D", "start": 510, "end": 990},
            {"date": "2026-12-19", "minutes": 450, "code": "D", "start": 510, "end": 990},
            {"date": "2026-12-21", "minutes": 450, "code": "V", "start": 450, "end": 930},
            {"date": "2026-12-22", "minutes": 450, "code": "V", "start": 450, "end": 930},
            {"date": "2026-12-26", "minutes": 450, "code": "D", "start": 510, "end": 990},
            {"date": "2026-12-28", "minutes": 450, "code": "V", "start": 450, "end": 930},
            {"date": "2026-12-29", "minutes": 450, "code": "V", "start": 450, "end": 930},
            {"date": "2026-12-31", "minutes": 450, "code": "V", "start": 450, "end": 930},
        ]
        self.assertEqual(res["days"], expected_days)

    def test_stray_january_d_ignored(self):
        """The 9th code token on Jan 1 is outside the month and ignored."""
        res = parse_roster(self.tokens, "Test Person")
        # Ensure only 8 days, and no day is in January 2027
        self.assertEqual(len(res["days"]), 8)
        dates = [d["date"] for d in res["days"]]
        self.assertNotIn("2027-01-01", dates)
        self.assertNotIn("2026-12-01", dates)

    def test_neighbours_anna_and_piet(self):
        """Anna Peeters and Piet Janssens parse with their own rows and totals match."""
        # Anna Peeters: total 22.5 h = 1350 minutes
        anna = parse_roster(self.tokens, "Anna Peeters")
        self.assertEqual(anna["total_minutes"], 1350)
        self.assertEqual(len(anna["days"]), 3)
        anna_dates = {d["date"] for d in anna["days"]}
        self.assertEqual(anna_dates, {"2026-12-23", "2026-12-24", "2026-12-25"})
        for d in anna["days"]:
            self.assertEqual(d["code"], "V")
            self.assertEqual(d["minutes"], 450)

        # Piet Janssens: total 45 h = 2700 minutes
        piet = parse_roster(self.tokens, "Piet Janssens")
        self.assertEqual(piet["total_minutes"], 2700)
        self.assertEqual(len(piet["days"]), 6)
        piet_dates = {d["date"] for d in piet["days"]}
        self.assertEqual(
            piet_dates,
            {"2026-12-10", "2026-12-11", "2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31"}
        )
        for d in piet["days"]:
            self.assertEqual(d["code"], "V")
            self.assertEqual(d["minutes"], 450)

        # Test Person days do not leak
        test_person = parse_roster(self.tokens, "Test Person")
        tp_dates = {d["date"] for d in test_person["days"]}
        # Verify Anna and Test Person have disjoint dates
        self.assertTrue(anna_dates.isdisjoint(tp_dates))

    def test_name_not_found(self):
        """Missing person name raises RosterError."""
        with self.assertRaises(RosterError) as ctx:
            parse_roster(self.tokens, "Nonexistent Person")
        self.assertIn("name not found", str(ctx.exception).lower())

    def test_name_casing_and_whitespace(self):
        """Name matches case-insensitively and collapses whitespace."""
        res = parse_roster(self.tokens, "  test   person  ")
        self.assertEqual(res["total_minutes"], 3600)
        self.assertEqual(len(res["days"]), 8)

    def test_total_mismatch(self):
        """Discrepancy between cell minutes sum and row total raises RosterError."""
        tokens = copy.deepcopy(self.tokens)
        # Find one hours token for Test Person (y = 171.4) and change 7.5 to 8.0
        for tok in tokens:
            if abs(tok[1] - 171.4) < 0.2 and tok[2] == "7.5":
                tok[2] = "8.0"
                break
        with self.assertRaises(RosterError) as ctx:
            parse_roster(tokens, "Test Person")
        self.assertIn("total mismatch", str(ctx.exception).lower())

    def test_unknown_code_with_hours(self):
        """Code not present in legend with hours > 0 raises RosterError."""
        tokens = copy.deepcopy(self.tokens)
        # Change code 'd' on day 6 (x ~ 204.3, y = 176.6) to 'XYZ'
        for tok in tokens:
            if abs(tok[1] - 176.6) < 0.2 and abs(tok[0] - 204.3) < 1.0:
                tok[2] = "XYZ"
                break
        with self.assertRaises(RosterError) as ctx:
            parse_roster(tokens, "Test Person")
        self.assertIn("unknown code", str(ctx.exception).lower())

    def test_legend_code_with_no_hours(self):
        """Legend code with no hours inside the month raises RosterError."""
        tokens = copy.deepcopy(self.tokens)
        # Remove the hours token '7.5' at day 6 (x ~ 202.6, y = 171.4)
        filtered = [
            tok for tok in tokens
            if not (abs(tok[1] - 171.4) < 0.2 and abs(tok[0] - 202.6) < 1.0)
        ]
        with self.assertRaises(RosterError) as ctx:
            parse_roster(filtered, "Test Person")
        self.assertIn("no hours", str(ctx.exception).lower())

    def test_code_without_hours_ignored(self):
        """Code like 'jv' without hours is ignored."""
        tokens = copy.deepcopy(self.tokens)
        # Add a 'jv' token on day 1 (x ~ 146.9, y = 176.6)
        tokens.append([146.9, 176.6, "jv"])
        res = parse_roster(tokens, "Test Person")
        self.assertEqual(res["total_minutes"], 3600)
        self.assertEqual(len(res["days"]), 8)
        dates = [d["date"] for d in res["days"]]
        self.assertNotIn("2026-12-01", dates)

    def test_two_code_tokens_on_one_day(self):
        """Two code tokens on the same day cell raises RosterError."""
        tokens = copy.deepcopy(self.tokens)
        # Add a second code token on day 6 (x ~ 204.3, y = 176.6)
        tokens.append([204.3, 176.6, "v"])
        with self.assertRaises(RosterError) as ctx:
            parse_roster(tokens, "Test Person")
        self.assertIn("two code tokens", str(ctx.exception).lower())

    def test_title_missing(self):
        """Missing title raises NotARoster."""
        tokens = copy.deepcopy(self.tokens)
        # Remove month token "December"
        filtered = [tok for tok in tokens if str(tok[2]).strip().lower() != "december"]
        with self.assertRaises(NotARoster):
            parse_roster(filtered, "Test Person")

    def test_wrong_weekday_under_day_1(self):
        """Wrong weekday token under day 1 raises RosterError."""
        tokens = copy.deepcopy(self.tokens)
        # Change month from December to November
        for tok in tokens:
            if str(tok[2]).strip().lower() == "december":
                tok[2] = "November"
                break
        with self.assertRaises(RosterError) as ctx:
            parse_roster(tokens, "Test Person")
        self.assertIn("weekday mismatch", str(ctx.exception).lower())

    def test_part_of_numbers(self):
        """Number row missing tokens raises RosterError."""
        tokens = copy.deepcopy(self.tokens)
        # Remove token "31" from the number row at y = 218.1
        filtered = [
            tok for tok in tokens
            if not (abs(tok[1] - 218.1) < 0.2 and str(tok[2]).strip() == "31")
        ]
        with self.assertRaises(RosterError):
            parse_roster(filtered, "Test Person")

    def test_zero_hour_perm_ignored(self):
        """0-hour code ('perm' with '0') is ignored."""
        tokens = copy.deepcopy(self.tokens)
        # Add 'perm' on day 1 (x ~ 146.9, y = 176.6) and '0' at (x ~ 146.9, y = 171.4)
        tokens.append([146.9, 176.6, "perm"])
        tokens.append([146.9, 171.4, "0"])
        res = parse_roster(tokens, "Test Person")
        self.assertEqual(res["total_minutes"], 3600)
        self.assertEqual(len(res["days"]), 8)
        dates = [d["date"] for d in res["days"]]
        self.assertNotIn("2026-12-01", dates)

    def test_decimal_hours(self):
        """Decimal hours: '8' -> 480, 7.25 -> 435, 7.5 -> 450."""
        tokens = copy.deepcopy(self.tokens)
        # Find three hours tokens at y = 171.4 and change them:
        # Day 6: change 7.5 to 8.0 (+30 min)
        # Day 19: change 7.5 to 7.25 (-15 min)
        # Day 21: change 7.5 to 7.25 (-15 min)
        # Total remains 3600 min (60 h)
        modified = 0
        for tok in tokens:
            if abs(tok[1] - 171.4) < 0.2 and tok[2] == "7.5":
                if modified == 0:
                    tok[2] = "8"
                    modified += 1
                elif modified == 1:
                    tok[2] = "7.25"
                    modified += 1
                elif modified == 2:
                    tok[2] = "7.25"
                    modified += 1
                    break

        res = parse_roster(tokens, "Test Person")
        self.assertEqual(res["total_minutes"], 3600)
        mins_map = {d["date"]: d["minutes"] for d in res["days"]}
        self.assertEqual(mins_map["2026-12-06"], 480)
        self.assertEqual(mins_map["2026-12-19"], 435)
        self.assertEqual(mins_map["2026-12-21"], 435)
        self.assertEqual(mins_map["2026-12-22"], 450)


if __name__ == "__main__":
    unittest.main()
