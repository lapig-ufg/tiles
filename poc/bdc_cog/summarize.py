import csv
import html
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .transport import atomic_write


def main():
    root = Path(__file__).parent
    records = [
        json.loads(p.read_text())
        for p in sorted((root / "evidence/campaign").glob("*.json"))
    ]
    groups = defaultdict(list)
    fields = [
        "source",
        "region",
        "year",
        "period",
        "z",
        "visparam",
        "stage",
        "status",
        "reference_format",
        "metrics",
        "performance",
        "jpeg_diagnostic",
        "error",
    ]
    with (root / "evidence/metrics.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    k: json.dumps(record.get(k), ensure_ascii=False)
                    if isinstance(record.get(k), dict)
                    else record.get(k)
                    for k in fields
                }
            )
            if record.get("metrics", {}).get("mae") is not None:
                groups[
                    (record["source"], record["stage"], record["visparam"], record["z"])
                ].append(record)
    summary = {
        "counts": {
            f"{stage}/{status}": n
            for (stage, status), n in Counter(
                (r["stage"], r["status"]) for r in records
            ).items()
        },
        "reference_formats": dict(
            Counter(
                r.get("reference_format") for r in records if r["status"] == "medido"
            )
        ),
        "groups": [],
    }
    lines = [
        "# Resultados medidos",
        "",
        "Agregação macro: cada comparação de tile recebe o mesmo peso. Amostras sem interseção válida não participam da média. O CSV preserva sensor/ano, bioma, período, zoom, visparam, estágio, formato real, cobertura, erros e métricas individuais. A campanha Sentinel sem calibração utiliza offset 0 e deve ser lida como diagnóstico inicial; os experimentos de offset e calibração são apresentados separadamente.",
        "",
        "| Fonte | Etapa | Estilo | Zoom | n | MAE RGB | RMSE RGB | SSIM RGB |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for key, rows in sorted(groups.items()):
        values = {
            metric: np.mean([r["metrics"][metric] for r in rows], axis=0).tolist()
            for metric in ["mae", "rmse"]
        }
        values["ssim"] = [
            float(
                np.mean(
                    [
                        r["metrics"]["ssim"][c]
                        for r in rows
                        if r["metrics"]["ssim"][c] is not None
                    ]
                )
            )
            if any(r["metrics"]["ssim"][c] is not None for r in rows)
            else None
            for c in range(3)
        ]
        summary["groups"].append(
            dict(
                source=key[0],
                stage=key[1],
                visparam=key[2],
                z=key[3],
                n=len(rows),
                **values,
            )
        )
        formatted = [
            " / ".join("n/d" if v is None else f"{v:.3f}" for v in values[m])
            for m in ["mae", "rmse", "ssim"]
        ]
        lines.append("| " + " | ".join(map(str, [*key, len(rows), *formatted])) + " |")
    (root / "RESULTADOS.md").write_text("\n".join(lines) + "\n")
    atomic_write(
        root / "evidence/summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2).encode(),
    )
    gallery = [
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Comparações BDC e GEE</title>',
        "<style>body{font:16px sans-serif;max-width:1000px;margin:32px auto;background:#f3f4f5;color:#17212b}figure{background:white;padding:16px;margin:24px 0}img{max-width:100%;height:auto}figcaption{margin-bottom:12px}</style>",
        "<h1>Comparações BDC e GEE</h1><p>GEE | BDC | diferença ampliada quatro vezes. Roxo indica ausência de par válido. Resultados iniciais Sentinel não corrigem o offset; propostas calibradas têm arquivos separados. Consulte as métricas e limitações antes de classificar a fidelidade.</p>",
    ]
    for path in sorted((root / "evidence/campaign").glob("*.png")):
        record = json.loads(path.with_suffix(".json").read_text())
        caption = html.escape(
            " / ".join(
                str(record[k])
                for k in [
                    "source",
                    "region",
                    "year",
                    "period",
                    "z",
                    "visparam",
                    "stage",
                ]
            )
        )
        gallery.append(
            f'<figure><figcaption>{caption}</figcaption><img loading="lazy" src="campaign/{path.name}" alt="{caption}"></figure>'
        )
    gallery.append("</html>")
    (root / "evidence/gallery.html").write_text("\n".join(gallery))
    print(json.dumps(summary["counts"]))


if __name__ == "__main__":
    main()
