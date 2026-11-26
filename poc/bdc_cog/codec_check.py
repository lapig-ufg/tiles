import io
import json
from pathlib import Path

from PIL import Image, JpegImagePlugin

from .compare import decode, metrics
from .transport import atomic_write, digest


def main():
    root = Path(__file__).parent
    results = []
    for path in (root / "evidence/campaign").glob("*.json"):
        record = json.loads(path.read_text())
        if record.get("status") != "medido" or not record.get("reference_url"):
            continue
        ref_path = (
            root
            / "cache/http"
            / (digest(["GET", record["reference_url"], None]) + ".body")
        )
        with Image.open(ref_path) as image:
            record["reference_format"] = image.format
            if image.format == "JPEG":
                candidate_path = Path(record["performance"]["path"])
                with Image.open(candidate_path) as candidate:
                    encoded = io.BytesIO()
                    candidate.convert("RGB").save(
                        encoded,
                        format="JPEG",
                        qtables=image.quantization,
                        subsampling=JpegImagePlugin.get_sampling(image),
                    )
                record["jpeg_diagnostic"] = metrics(
                    decode(ref_path.read_bytes()), decode(encoded.getvalue())
                )
                record["jpeg_tables_digest"] = digest(image.quantization)
        atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2).encode())
        results.append(
            {
                k: record[k]
                for k in [
                    "reference_url",
                    "reference_format",
                    "source",
                    "year",
                    "period",
                    "z",
                    "visparam",
                    "stage",
                ]
            }
        )
    atomic_write(
        root / "evidence/reference-formats.json", json.dumps(results, indent=2).encode()
    )


if __name__ == "__main__":
    main()
