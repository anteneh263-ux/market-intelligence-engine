"""Run unchanged fault tests against the versioned deployment copies, not AWS.

Original tests remain untouched. Their identity/request tests are replaced by
explicit medium/source cases in test_phase6_receipt_medium.py.
"""
import importlib.util
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

host_tests=load('medium_host_regression_source',REPO/'tests/test_phase6_host_preparation.py')
host_tests.host=load('medium_host_regression',REPO/'deployment/phase6/receipt-medium-v1/prepare-qualification-host.py')
for name,test in vars(host_tests).items():
    if name.startswith('test_') and not name.startswith(('test_delivery','test_imds')):
        globals()[name]=test

timer_tests=load('medium_timer_regression_source',REPO/'tests/test_phase6_receipt_recovery.py')
timer_tests.timer=load('medium_timer_regression',REPO/'deployment/phase6/receipt-medium-v1/receipt-recovery-timer.py')
for name,test in vars(timer_tests).items():
    if name.startswith('test_') and not name.startswith(('test_metadata','test_delivery','test_recovery','test_watch')):
        globals()[name]=test
