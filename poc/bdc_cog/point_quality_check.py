import argparse
import json
from pathlib import Path

from app.modules.bdc.local_quality import compare
from app.modules.bdc.transport import Transport, atomic_write


def main():
    parser = argparse.ArgumentParser(description='Compara cobertura global e qualidade no buffer do ponto no BDC')
    parser.add_argument('--collection', choices=['landsat-2', 'S2_L2A-1'], default='landsat-2')
    parser.add_argument('--lon', type=float, default=-49.4)
    parser.add_argument('--lat', type=float, default=-16.7)
    parser.add_argument('--radius', type=float, default=1000)
    parser.add_argument('--start', default='2024-01-01')
    parser.add_argument('--end', default='2024-01-31')
    parser.add_argument('--criterion', choices=['cloud', 'unusable'], default='cloud')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    remote = Transport(Path('poc/bdc_cog/cache/point-quality'))
    try:
        result = compare(remote, args.collection, args.lon, args.lat, args.radius,
                         args.start, args.end, args.criterion, max_seconds=600)
        result['origin_bytes'] = sum(e.get('bytes', 0) for e in remote.events if not e.get('cache'))
        atomic_write(args.output, json.dumps(result, indent=2, ensure_ascii=False).encode())
        print(json.dumps({key: value for key, value in result.items() if key not in {'rows', 'geometry'}}, ensure_ascii=False))
        for row in result['rows']:
            print(row['id'], row.get('scene_cloud_pct'), row.get('cloud_pct'), row.get('unusable_pct'), row.get('valid_pct'))
    finally:
        remote.session.close()


if __name__ == '__main__':
    main()
