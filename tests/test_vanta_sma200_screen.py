"""Isolated screen mathematics; no MIE runtime or evidence imports."""
import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location('screen', Path(__file__).parents[1]/'scripts/vanta_sma200_screen.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def rows(prices):
    return [dict(timestamp=i*86400, open=p, high=p, low=p, close=p) for i,p in enumerate(prices)]


class ScreenTests(unittest.TestCase):
    def test_no_same_bar_or_future_read(self):
        a = rows([100, 110, 120, 130, 140])
        b = rows([100, 110, 120, 1, 999])
        self.assertEqual(m.signals(a, 2)[:4], m.signals(b, 2)[:4])

    def test_known_drawdown(self):
        r = m.screen(rows([90,100,100,94,94]), 2, length=2)
        self.assertEqual(r['first_breaches']['challenge_eod_gt_5pct'], '1970-01-04')
        self.assertAlmostEqual(r['unrestricted_close_max_drawdown_pct'], 6)

    def test_costs_reduce_flat_price_equity(self):
        a = rows([90,100,100,100,100])
        free = m.screen(a, 2, length=2)
        paid = m.screen(a, 2, side_bps=8, carry_annual=.1095, length=2)
        self.assertLess(paid['unrestricted_return_pct'], free['unrestricted_return_pct'])

    def test_exposure_scales_single_trade_loss(self):
        a = rows([90,100,100,90])
        for exposure in (.1, .2):
            r = m.screen(a, 2, length=2, exposure=exposure)
            self.assertAlmostEqual(r['unrestricted_return_pct'], -10*exposure)
            self.assertNotIn('challenge_eod_gt_5pct', r['first_breaches'])

    def test_invalid_exposure(self):
        for exposure in (0, -1, 1.1, float('nan')):
            with self.assertRaises(ValueError):
                m.screen(rows([90,100,100]), 2, length=2, exposure=exposure)

    def test_inactivity_even_while_flat(self):
        a = rows([100]*65)
        r = m.screen(a, 2, length=2)
        self.assertEqual(r['orders'], 0)
        self.assertEqual(r['first_breaches']['inactivity_60_days'], '1970-03-04')

if __name__ == '__main__':
    unittest.main()
