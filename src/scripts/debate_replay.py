"""Replay one persisted debate record without calling live dependencies."""

import argparse
import json
from pathlib import Path

from src.debate_presenter import DebatePresenter
from src.debate_report import render_debate_html


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="Path to a debate JSON record")
    parser.add_argument("--no-color", action="store_true")
    return parser.parse_args()


def load_record(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    args = parse_args()
    record = load_record(args.record)
    DebatePresenter(color=not args.no_color).replay(record)

    output_path = args.record.with_suffix(".html")
    output_path.write_text(render_debate_html(record), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    main()
