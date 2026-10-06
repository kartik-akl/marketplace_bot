import argparse
import asyncio
import logging
from pathlib import Path

from .app import run, setup, single_instance
from .config import load_config
from .notifications import Notifier
from .models import Listing
from .storage import Store
from .branding import BANNER, NAME


def main():
    print(BANNER)
    parser = argparse.ArgumentParser(description=f"{NAME} - Facebook Marketplace phone alerts")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Visible login and search-filter verification")
    run_parser = commands.add_parser("run", help="Headless polling; visible manual challenge recovery")
    run_parser.add_argument("--once", action="store_true", help="Scan each configured item once, spaced by the polling interval")
    test_parser = commands.add_parser("test-notification", help="Send a test to the configured phone notification service")
    test_parser.add_argument("--recent", action="store_true", help="Send a sample 10-minute-old listing with the recent-listing Discord ping")
    commands.add_parser("status", help="Show delivery states, including uncertain deliveries")
    retry = commands.add_parser("retry", help="Manually retry an uncertain delivery after checking your channel")
    retry.add_argument("listing_id")
    retry.add_argument("--accept-duplicate-risk", action="store_true", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config)
        with single_instance(config.path("database_path")):
            if args.command == "setup":
                asyncio.run(setup(config))
            elif args.command == "run":
                asyncio.run(run(config, once=args.once))
            elif args.command == "test-notification":
                notifier = Notifier(config)
                if args.recent:
                    item = Listing(id="test-recent", title=f"TEST: {NAME} - recent listing",
                                   price="NZ$100", price_amount=100, location=config.location,
                                   url="https://www.facebook.com/marketplace/", posted="Listed 10 minutes ago")
                    asyncio.run(notifier.send_listing(item))
                else:
                    asyncio.run(notifier.send_status(f"{NAME} test: phone notifications are connected."))
                print("Test notification confirmed.")
            else:
                store = Store(config.path("database_path"))
                try:
                    if args.command == "status":
                        for row in store.status():
                            print(f"{row['id']}  {row['state']}  {row['error'] or ''}")
                    elif not store.retry_uncertain(args.listing_id):
                        raise ValueError("Listing not found or its delivery is not uncertain")
                    else:
                        print("Listing queued. A duplicate is possible if the original message arrived.")
                finally:
                    store.close()
    except KeyboardInterrupt:
        print("Stopped. Saved session and SQLite history retained.")
    except Exception as exc:
        # Only known safe errors may include details. Never dump transport/browser
        # exceptions: they can expose webhook tokens or private Facebook content.
        safe = isinstance(exc, (ValueError, FileNotFoundError)) or type(exc).__name__ == "DeliveryError"
        parser.exit(1, f"Error: {str(exc) if safe else type(exc).__name__ + '; check installation/browser availability'}\n")
