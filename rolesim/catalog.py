"""Index author-provided documents as reference data, never executable instructions."""
import hashlib
import json
import re
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def build_catalog(source_dir):
    records, sources = [], []
    for name, prefix in (("心智建模库.md", "mind"), ("决策机制库.md", "decision")):
        path = Path(source_dir) / name
        raw = path.read_bytes()
        lines = raw.decode("utf-8").splitlines()
        sha = hashlib.sha256(raw).hexdigest()
        sources.append({"file": name, "sha256": sha})
        starts = [(i, re.match(r"^(#{1,3})\s+(.+)$", line))
                  for i, line in enumerate(lines)]
        starts = [(i, m) for i, m in starts if m]
        for index, (start, match) in enumerate(starts):
            title = match[2]
            number = re.match(r"(\d+(?:\.\d+)*)\b", title)
            if not number:
                continue
            end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
            records.append({"id": f"{prefix}.{number[1]}", "title": title,
                            "source": name, "source_sha256": sha,
                            "line_start": start + 1, "line_end": end,
                            "text": "\n".join(lines[start:end]),
                            "status": "reference_only"})
    return {"schema_version": "1.0", "sources": sources, "sections": records}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")
