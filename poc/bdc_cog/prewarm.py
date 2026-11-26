import argparse
import json
from pathlib import Path

from .render import render_cached
from .transport import Transport, atomic_write


def prewarm(manifest, root, max_jobs, max_bytes):
    jobs = json.loads(Path(manifest).read_text())
    if len(jobs) > max_jobs:
        raise ValueError(f"Plano excede orçamento: {len(jobs)} > {max_jobs}")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    transport = Transport(root / "http")
    results = []
    for job in jobs:
        used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        if used >= max_bytes:
            raise RuntimeError("Orçamento de disco atingido; aquecimento interrompido")
        items = json.loads(Path(job["items_file"]).read_text())
        if isinstance(items, dict):
            items = [items]
        _, stats = render_cached(
            transport,
            root / "png",
            items,
            job["source"],
            job["visparam"],
            *job["xyz"],
            job.get("mode", "SCENE"),
            job.get("s2_offset"),
        )
        results.append(dict(job=job, performance=stats))
        atomic_write(
            root / "prewarm-results.json", json.dumps(results, indent=2).encode()
        )
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pré-geração sequencial, retomável e com orçamento"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-jobs", type=int, default=100)
    parser.add_argument("--max-bytes", type=int, default=1024**3)
    args = parser.parse_args()
    print(
        json.dumps(
            prewarm(args.manifest, args.root, args.max_jobs, args.max_bytes), indent=2
        )
    )
