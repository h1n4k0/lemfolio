import argparse
import os
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Lemfolio local research workspace")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=None)
    args = parser.parse_args()
    if args.data:
        os.environ["LEMFOLIO_DATA"] = str(args.data.resolve())
    import uvicorn
    from .api import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)

if __name__ == "__main__":
    main()
