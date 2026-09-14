# Copyright (C) 2025 David Němec
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pykeepass import PyKeePass, create_database
from pykeepass.exceptions import CredentialsError

from src.bw_client import BwClient
from src.folder import load_folders
from src.item import CustomFieldType, Item, ItemType
from src.set_kp_entry_urls import set_kp_entry_urls

if TYPE_CHECKING:
    from argparse import Namespace

    from pykeepass.entry import Entry

logger = logging.getLogger(__name__)

TOTP_SEED_PROPERTY = "TOTP Seed"
TOTP_SETTINGS_PROPERTY = "TOTP Settings"
MAX_TITLE_ATTEMPTS = 5

REDACTED = "***"


def _redacted_item(item: dict) -> dict:
    """Return a deep copy of *item* with secrets redacted for logging."""
    redacted = json.loads(json.dumps(item))
    login = redacted.get("login")
    if isinstance(login, dict):
        if "password" in login:
            login["password"] = REDACTED
        login.pop("totp", None)
    for field in redacted.get("fields", []):
        if field.get("type") == CustomFieldType.HIDDEN:
            field["value"] = REDACTED
    return redacted


def _is_duplicate_title_error(exception: Exception) -> bool:
    return "already exists" in str(exception)


def _add_entry_with_title_fallback(
    kp: PyKeePass,
    groups_by_id: dict,
    bw_item: Item,
) -> Entry:
    """Add the entry, incrementing the title suffix on collisions.

    A fixed fallback title would collide forever on repeated exports, so the
    suffix is incremented until an attempt succeeds or attempts run out.
    """
    destination_group = groups_by_id[bw_item.get_folder_id()]
    base_title = bw_item.get_name()
    entry_title = base_title
    for attempt in range(1, MAX_TITLE_ATTEMPTS + 1):
        try:
            return kp.add_entry(
                destination_group=destination_group,
                title=entry_title,
                username=bw_item.get_username(),
                password=bw_item.get_password(),
                notes=bw_item.get_notes(),
            )
        except Exception as e:
            if not _is_duplicate_title_error(e):
                raise
            if attempt == MAX_TITLE_ATTEMPTS:
                message = (
                    f"Could not add entry titled {base_title!r}: "
                    "title collisions exhausted."
                )
                raise RuntimeError(message) from e
            entry_title = f"{base_title} - ({bw_item.get_id()}) [{attempt}]"
    raise AssertionError("Unreachable: the loop above always returns or raises.")


def bitwarden_to_keepass(args: Namespace, client: BwClient | None = None) -> None:
    try:
        kp = PyKeePass(
            args.database_path,
            password=args.database_password,
            keyfile=args.database_keyfile,
        )
    except FileNotFoundError:
        logger.info("KeePass database does not exist, creating a new one.")
        kp = create_database(
            args.database_path,
            password=args.database_password,
            keyfile=args.database_keyfile,
        )
    except CredentialsError as e:
        raise RuntimeError("Wrong password for KeePass database.") from e

    bw = client or BwClient(args.bw_path, args.bw_session)

    groups_by_id = load_folders(kp, bw.list_folders())
    logger.info("Folders done (%d).", len(groups_by_id))

    items = bw.list_items()
    logger.info("Starting to process %d items.", len(items))
    for item in items:
        if item["type"] in [ItemType.CARD, ItemType.IDENTITY]:
            logger.warning("Skipping credit card or identity item %s.", item["name"])
            continue

        bw_item = Item(item)

        try:
            entry = _add_entry_with_title_fallback(kp, groups_by_id, bw_item)

            totp_secret, totp_settings = bw_item.get_totp()
            if totp_secret and totp_settings:
                entry.set_custom_property(TOTP_SEED_PROPERTY, totp_secret, protect=True)
                entry.set_custom_property(TOTP_SETTINGS_PROPERTY, totp_settings)

            set_kp_entry_urls(entry, bw_item.get_uris())

            for field in bw_item.get_custom_fields():
                entry.set_custom_property(
                    field["name"],
                    field["value"],
                    protect=field["type"] == CustomFieldType.HIDDEN,
                )

            for attachment in bw_item.get_attachments():
                attachment_raw = bw.get_attachment(bw_item.get_id(), attachment["id"])
                attachment_id = kp.add_binary(attachment_raw)
                entry.add_attachment(attachment_id, attachment["fileName"])

        except Exception as e:
            logger.warning(
                "Skipping item named %s due to the following error: %r",
                item["name"],
                e,
            )
            logger.warning(
                "Offending object:\n%s",
                json.dumps(_redacted_item(item), indent=4, sort_keys=True),
            )
            continue

    logger.info("Saving changes to KeePass database.")
    kp.save()
    logger.info("Export completed.")
