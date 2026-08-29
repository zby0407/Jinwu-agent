from importlib.util import module_from_spec, spec_from_file_location
import sys
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
SPEC = spec_from_file_location(
    "sc26_forecast_runner", "scripts/run_sc26_historical_forecast.py"
)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _cycles() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "cycle": range(1, 26),
            "peak": [100.0 + 3.0 * i for i in range(25)],
            "rise_slope": [1.0 + 0.1 * i for i in range(25)],
        }
    )


def test_backtests_derive_leakage_audit_from_training_cycle_bounds():
    MODULE.BOOTSTRAP_REPS = 100
    cycles = _cycles()

    same_frame, same_stats = MODULE.same_cycle_backtest(
        cycles, MODULE.np.random.default_rng(1)
    )
    lag_frame, lag_stats = MODULE.next_cycle_backtest(
        cycles, MODULE.np.random.default_rng(2), "lag_peak"
    )

    for frame, stats in ((same_frame, same_stats), (lag_frame, lag_stats)):
        assert (frame["training_cycle_end"] < frame["cycle"]).all()
        assert stats["leakage_audit_passed"] is True
