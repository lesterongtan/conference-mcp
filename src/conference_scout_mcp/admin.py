from __future__ import annotations

import argparse
import json
import secrets

from .storage import SearchStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Conference Scout customer administration")
    commands = parser.add_subparsers(dest="command", required=True)

    create_customer = commands.add_parser("create-customer")
    create_customer.add_argument("--customer-id", required=True)
    create_customer.add_argument("--name", required=True)

    create_key = commands.add_parser("create-key")
    create_key.add_argument("--customer-id", required=True)
    create_key.add_argument("--name", default="default")

    revoke_key = commands.add_parser("revoke-key")
    revoke_key.add_argument("--customer-id", required=True)
    revoke_key.add_argument("--key-id", required=True)

    args = parser.parse_args()
    store = SearchStore()
    if args.command == "create-customer":
        store.ensure_customer(args.customer_id, args.name)
        print(json.dumps({"customer_id": args.customer_id, "created": True}))
        return
    if args.command == "create-key":
        raw_key = f"cs_{secrets.token_urlsafe(32)}"
        key_id = store.create_api_key(args.customer_id, raw_key, args.name)
        print(
            json.dumps(
                {
                    "customer_id": args.customer_id,
                    "key_id": key_id,
                    "api_key": raw_key,
                    "warning": "This plaintext key is shown once. Store it securely.",
                }
            )
        )
        return
    revoked = store.revoke_api_key(args.key_id, args.customer_id)
    print(json.dumps({"customer_id": args.customer_id, "key_id": args.key_id, "revoked": revoked}))
