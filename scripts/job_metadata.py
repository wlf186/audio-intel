"""Read storage or waveform metadata from a local audio-intel service."""
from __future__ import annotations

import argparse
import json
import os

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    storage = sub.add_parser("storage")
    storage.add_argument("job_ids", nargs="+")
    storage.add_argument("--refresh", action="store_true")
    wave = sub.add_parser("waveform")
    wave.add_argument("job_id")
    wave.add_argument("--track", choices=("original", "enhanced"), default="original")
    wave.add_argument("--start", type=float, default=0)
    wave.add_argument("--end", type=float)
    wave.add_argument("--points", type=int, default=1024)
    args = parser.parse_args()
    key = os.getenv("AUDIO_INTEL_API_KEY")
    with httpx.Client(base_url=os.getenv("AUDIO_INTEL_BASE_URL", "http://127.0.0.1:20810"),
                      headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=120) as client:
        if args.action == "storage":
            response = client.post('/api/v1/jobs/storage', json={'job_ids': args.job_ids, 'refresh': args.refresh})
        else:
            params = {key: value for key, value in vars(args).items() if key not in {'action', 'job_id'} and value is not None}
            response = client.get(f'/api/v1/jobs/{args.job_id}/waveform', params=params)
        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
