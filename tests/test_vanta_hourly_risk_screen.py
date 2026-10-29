import importlib.util
from pathlib import Path
import unittest
s=importlib.util.spec_from_file_location('hourly',Path(__file__).parents[1]/'scripts/vanta_hourly_risk_screen.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)


def bars(days):
    return [{'t':(d*24+h)*m.HOUR,'o':p,'h':p,'l':p,'c':p} for d,p in enumerate(days) for h in range(24)]


class HourlyRiskTests(unittest.TestCase):
    def test_daily_causality(self):
        a=bars([90,100,110,120]);b=bars([90,100,1,1])
        self.assertEqual(m.daily_targets(a,2)[2],m.daily_targets(b,2)[2])

    def test_flat_market_no_orders(self):
        b=bars([100]*5);f={x['t']:0 for x in b}
        r=m.simulate(b,f,length=2)
        self.assertEqual(r['orders_count'],0);self.assertEqual(r['return_pct'],0)

    def test_stop_delays_and_locks(self):
        b=bars([90,100,100,50,50])
        for x in b:
            if 2*m.DAY+12*m.HOUR <= x['t'] < 3*m.DAY:
                x.update(o=50,h=50,l=50,c=50)
        f={x['t']:0 for x in b}
        # Entry day2 open; gap loss observed at hour12, exit hour13.
        r=m.simulate(b,f,length=2,side_bps=0)
        self.assertEqual(r['stop_trigger_hours'][0],m.iso(2*m.DAY+12*m.HOUR))
        self.assertEqual(r['stop_exits'][0],m.iso(2*m.DAY+13*m.HOUR))
        self.assertGreater(r['max_hour_close_drawdown_pct'],2)
        self.assertEqual(len(r['stop_exits']),1)
        self.assertFalse(r['ending_open_position'])

    def test_funding_sign_and_cost(self):
        b=bars([90,100,100,100,100]);f={x['t']:.0001 for x in b}
        free=m.simulate(b,f,length=2,side_bps=0,use_funding=False)
        paid=m.simulate(b,f,length=2,side_bps=8,use_funding=True)
        self.assertLess(paid['return_pct'],free['return_pct'])
        self.assertGreater(paid['net_funding_debit_pct_initial_equity'],0)

    def test_missing_funding_refused(self):
        raw=[{'t':0,'T':m.HOUR-1,'o':'1','h':'1','l':'1','c':'1'}]
        with self.assertRaises(ValueError):m.validate(raw,[],2*m.HOUR)

    def test_unclosed_candle_excluded(self):
        raw=[{'t':t,'T':t+m.HOUR-1,'o':'1','h':'1','l':'1','c':'1'} for t in (0,m.HOUR)]
        b,_=m.validate(raw,[{'time':0,'fundingRate':'0'}],m.HOUR)
        self.assertEqual(len(b),1)

if __name__=='__main__':unittest.main()
