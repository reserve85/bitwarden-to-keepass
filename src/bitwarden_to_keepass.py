# Copyright (C) 2025 David Němec
from __future__ import annotations

import contextlib
import copy
import json
import logging
import re
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
    from pykeepass.group import Group as KPGroup

logger = logging.getLogger(__name__)

TOTP_SEED_PROPERTY = "TOTP Seed"
TOTP_SETTINGS_PROPERTY = "TOTP Settings"
# Every exported entry is stamped with its Bitwarden id so that repeated
# exports update the existing entry instead of piling up near-duplicates.
BITWARDEN_ID_PROPERTY = "Bitwarden ID"
MAX_TITLE_ATTEMPTS = 5

REDACTED = "***"

# Field names whose values are treated as secrets when an item is logged.
_SENSITIVE_FIELD_NAME = re.compile(
    r"(?i)(password|passphrase|pass|pw|secret|token|t?otp|seed|pin|"
    r"api[ _-]?key|private[ _-]?key|client[ _-]?(id|secret))",
)


def _is_sensitive_field_name(name: str) -> bool:
    return bool(_SENSITIVE_FIELD_NAME.search(name))


def _redacted_item(item: dict) -> dict:
    """Return a deep copy of *item* with secrets redacted for logging."""
    redacted = copy.deepcopy(item)
    login = redacted.get("login")
    if isinstance(login, dict):
        if "password" in login:
            login["password"] = REDACTED
        login.pop("totp", None)
    # Notes are free-form and frequently hold secrets (recovery codes, API
    # tokens, security answers, ...).
    if isinstance(redacted.get("notes"), str):
        redacted["notes"] = REDACTED
    for field in redacted.get("fields", []):
        name = field.get("name") or ""
        if field.get("type") == CustomFieldType.HIDDEN or _is_sensitive_field_name(
            name,
        ):
            field["value"] = REDACTED
    return redacted


def _is_duplicate_title_error(exception: Exception) -> bool:
    return "already exists" in str(exception)


def _destination_group(
    groups_by_id: dict[str | None, KPGroup],
    folder_id: str | None,
) -> KPGroup:
    """Resolve a folder id to a group, falling back to the root group.

    A missing or stale ``folderId`` (folder deleted after ``list_folders``,
    malformed item, ...) must not silently drop an item.
    """
    return groups_by_id.get(folder_id, groups_by_id[None])


def _add_entry_with_title_fallback(
    kp: PyKeePass,
    groups_by_id: dict[str | None, KPGroup],
    bw_item: Item,
) -> Entry:
    """Add the entry, incrementing the title suffix on collisions.

    A fixed fallback title would collide forever on repeated exports, so the
    suffix is incremented until an attempt succeeds or attempts run out.
    """
    destination_group = _destination_group(groups_by_id, bw_item.get_folder_id())
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


def _find_entry_by_bitwarden_id(kp: PyKeePass, bitwarden_id: str) -> Entry | None:
    """Return the entry previously exported for *bitwarden_id*, if any."""
    if not bitwarden_id:
        return None
    for entry in kp.entries:
        if entry.get_custom_property(BITWARDEN_ID_PROPERTY) == bitwarden_id:
            return entry
    return None


def _update_entry_from_item(
    entry: Entry,
    kp: PyKeePass,
    groups_by_id: dict[str | None, KPGroup],
    bw_item: Item,
) -> None:
    """Refresh an entry that was created by a previous export run."""
    destination_group = _destination_group(groups_by_id, bw_item.get_folder_id())
    # pykeepass 4.x exposes no ``Entry.group`` setter; ``move_entry`` is the
    # public API. Appending the element to the same group only reorders it,
    # so calling it unconditionally is safe.
    kp.move_entry(entry, destination_group)
    entry.title = bw_item.get_name()
    entry.username = bw_item.get_username()
    entry.password = bw_item.get_password()
    entry.notes = bw_item.get_notes()


def _set_custom_property_or_remove(
    entry: Entry,
    name: str,
    value: object,
    *,
    protect: bool = False,
) -> None:
    """Set a custom property, or remove it when the value is empty."""
    if value is None or value == "":
        with contextlib.suppress(AttributeError):
            entry.delete_custom_property(name)
        return
    entry.set_custom_property(name, value, protect=protect)


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

        # Repeated exports update the entry created by an earlier run instead
        # of piling up near-duplicates.
        entry = _find_entry_by_bitwarden_id(kp, bw_item.get_id())
        created = entry is None

        try:
            if created:
                entry = _add_entry_with_title_fallback(kp, groups_by_id, bw_item)
            else:
                _update_entry_from_item(entry, kp, groups_by_id, bw_item)

            entry.set_custom_property(BITWARDEN_ID_PROPERTY, bw_item.get_id())

            totp_secret, totp_settings = bw_item.get_totp()
            _set_custom_property_or_remove(
                entry,
                TOTP_SEED_PROPERTY,
                totp_secret,
                protect=True,
            )
            _set_custom_property_or_remove(
                entry,
                TOTP_SETTINGS_PROPERTY,
                totp_settings,
            )

            set_kp_entry_urls(entry, bw_item.get_uris(), reset=True)

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
            # Never leave a half-populated entry behind: a newly created entry
            # is rolled back so a partially-failed item is not saved.
            if created and entry is not None:
                try:
                    kp.delete_entry(entry)
                except Exception:
                    logger.warning(
                        "Could not roll back the newly created entry for item %s.",
                        item["name"],
                        exc_info=True,
                    )
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
