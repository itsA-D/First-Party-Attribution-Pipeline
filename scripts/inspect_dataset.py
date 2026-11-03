from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ISO_RE = re.compile(r"^[+-]?\d{4}-\d{2}-\d{2}T")
EPOCH_RE = re.compile(r"^\d{10,13}$")


def _event_profile(path: Path, samples: int) -> dict[str, Any]:
    count = 0
    keys: Counter[str] = Counter()
    event_names: Counter[str] = Counter()
    event_ids: Counter[str] = Counter()
    timestamp_shapes: Counter[str] = Counter()
    bad_years = 0
    samples_out = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            count += 1
            keys.update(payload.keys())
            event_names[str(payload.get("event_name"))] += 1
            event_ids[str(payload.get("event_id"))] += 1
            ts = str(payload.get("ts") or "")
            if EPOCH_RE.match(ts):
                timestamp_shapes["epoch"] += 1
            elif ISO_RE.match(ts):
                if ts.endswith("Z"):
                    timestamp_shapes["iso_z"] += 1
                elif re.search(r"[+-]\d{2}:\d{2}$", ts):
                    timestamp_shapes["iso_offset"] += 1
                else:
                    timestamp_shapes["iso_naive_or_other"] += 1
            else:
                timestamp_shapes["other"] += 1
            try:
                year = datetime.fromisoformat(ts.replace("Z", "+00:00")).year
                if year < 2000 or year > 2100:
                    bad_years += 1
            except ValueError:
                pass
            if len(samples_out) < samples:
                samples_out.append(
                    {
                        "line": line_number,
                        "event_id": payload.get("event_id"),
                        "event_name": payload.get("event_name"),
                        "ts": payload.get("ts"),
                        "anonymous_id": payload.get("anonymous_id"),
                        "has_email_hash": bool(payload.get("email_hash")),
                        "page_url": payload.get("page_url"),
                        "referrer": payload.get("referrer"),
                    }
                )
    return {
        "file": path.name,
        "rows": count,
        "fields": dict(keys),
        "event_names": dict(event_names),
        "unique_event_ids": len(event_ids),
        "event_ids_seen_more_than_once": sum(1 for value in event_ids.values() if value > 1),
        "timestamp_shapes": dict(timestamp_shapes),
        "corrupted_year_timestamps": bad_years,
        "samples": samples_out,
    }


def _csv_profile(path: Path, key_fields: list[str], samples: int) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        rows = list(reader)
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for field in key_fields:
            if field in row:
                counts[field][row.get(field) or ""] += 1
    return {
        "file": path.name,
        "rows": len(rows),
        "fields": fields,
        "distinct": {field: len(values) for field, values in counts.items()},
        "top_values": {
            field: values.most_common(12) for field, values in counts.items()
        },
        "samples": rows[:samples],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile the Urban Thread source files")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = {
        "events": _event_profile(args.data_dir / "events.jsonl", args.samples),
        "orders": _csv_profile(
            args.data_dir / "orders.csv",
            ["financial_status", "currency", "order_id", "email_hash"],
            args.samples,
        ),
        "ad_spend": _csv_profile(
            args.data_dir / "ad_spend.csv",
            ["platform", "campaign", "currency", "date"],
            args.samples,
        ),
    }
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
