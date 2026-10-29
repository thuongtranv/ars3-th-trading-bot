"""CLI: python -m ars3_alert {verify,run,backtest} [--loop] [--offline] [--config PATH]"""
import argparse
from pathlib import Path

from common.env import load_yaml
from common.log import setup_logging

from . import backtest, runner, sources


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ars3_alert", description=__doc__)
    parser.add_argument("command", choices=["verify", "run", "backtest"])
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.yaml")))
    parser.add_argument("--loop", action="store_true", help="run: repeat every run.every_hours")
    parser.add_argument("--offline", action="store_true", help="use cached data only, no network")
    args = parser.parse_args(argv)

    cfg = load_yaml(args.config)
    Path(cfg["paths"]["logfile"]).parent.mkdir(parents=True, exist_ok=True)
    setup_logging(cfg["paths"]["logfile"])

    if args.command == "verify":
        sources.verify(cfg)
    elif args.command == "backtest":
        backtest.run(cfg, args.offline)
    elif args.loop:
        runner.loop(cfg, args.offline)
    else:
        runner.run_once(cfg, args.offline)


if __name__ == "__main__":
    main()
