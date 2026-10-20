"""Inspect durable debate memory without loading models or indexes."""

import argparse
import json
from pathlib import Path

from src.memory_store import SQLiteMemoryStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = (
    PROJECT_ROOT / "data" / "memory" / "agent_memory.sqlite3"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="查看多 Agent 辩论的 SQLite 持久记忆"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--run-id", help="查看某一次运行的汇总和最后检查点")
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args()


def main():
    args = parse_args()
    with SQLiteMemoryStore(args.db) as store:
        if args.run_id:
            result = store.get_run_summary(args.run_id)
            result["latest_checkpoint"] = store.load_latest_checkpoint(
                args.run_id
            )
        else:
            result = store.list_runs(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
