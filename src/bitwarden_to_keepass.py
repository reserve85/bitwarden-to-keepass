# Copyright (C) 2025 David Němec
from __future__ import annotations

import contextlib
import copy
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pykeepass import PyKeePass, create_database
from pykeepass.exceptions import CredentialsError

from src.bw_client import BwClient
from src.folder import load_folders
from src.item import CustomFieldType, Item, ItemType
from src.set_kp_entry_urls import (
    ANDROID_APP_PROPERTY,
    EXTRA_URL_PROPERTY_PREFIX,
    IOS_APP_PROPERTY_PREFIX,
    set_kp_entry_urls,
)

if TYPE_CHECKING:
    from argparse import Namespace

    from lxml.etree import _Element
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
        # Redact everything that is not a plain TEXT field: hidden and
        # unknown/forward field types are treated as secret-like (see
        # Item.get_custom_fields), so a fresh Bitwarden field type cannot
        # leak through the failure log.
        if field.get("type") != CustomFieldType.TEXT or _is_sensitive_field_name(
            name,
        ):
            field["value"] = REDACTED
    return redacted


def _is_duplicate_title_error(exception: Exception) -> bool:
    message = str(exception)
    # pykeepass raises 'An entry "<title>" already exists in "<group>"' when a
    # group already contains an entry with the same title (and username). The
    # prefix guard keeps unrelated "already exists" errors (file collisions,
    # ...) from being mistaken for a title collision.
    return message.startswith('An entry "') and "already exists" in message


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


URL_PROPERTY_NAME_PREFIXES = (
    ANDROID_APP_PROPERTY + "_",
    IOS_APP_PROPERTY_PREFIX,
    EXTRA_URL_PROPERTY_PREFIX,
)


def _is_url_property_name(name: str) -> bool:
    """True for the custom properties set_kp_entry_urls manages."""
    return name == ANDROID_APP_PROPERTY or name.startswith(URL_PROPERTY_NAME_PREFIXES)


def _snapshot_entry(entry: Entry) -> _Element:
    """Capture the entry's XML so a failed update can be undone."""
    return copy.deepcopy(entry._element)  # noqa: SLF001


def _restore_entry(entry: Entry, snapshot: _Element, group: KPGroup) -> None:
    """Undo an in-place update by restoring the captured entry element."""
    parent = entry._element.getparent()  # noqa: SLF001
    if parent is not None:
        parent.remove(entry._element)  # noqa: SLF001
    entry._element = snapshot  # noqa: SLF001
    group.append(entry)


def _rollback_entry(
    kp: PyKeePass,
    entry: Entry | None,
    snapshot: _Element | None,
    group: KPGroup | None,
    item: dict,
) -> None:
    """Undo a half-applied item: delete a fresh entry or restore an updated one.

    The update path always holds a snapshot of the pre-update entry, the create
    path never does - so the snapshot's presence selects the restore path.
    """
    if snapshot is not None:
        try:
            _restore_entry(entry, snapshot, group)
        except Exception:
            logger.warning(
                "Could not roll back the updated entry for item %s.",
                item.get("name"),
                exc_info=True,
            )
    elif entry is not None:
        try:
            kp.delete_entry(entry)
        except Exception:
            logger.warning(
                "Could not roll back the newly created entry for item %s.",
                item.get("name"),
                exc_info=True,
            )


def _sync_custom_fields(entry: Entry, bw_item: Item, *, remove_stale: bool) -> None:
    """Mirror the item's custom fields onto *entry*.

    With *remove_stale* (the update path) previously exported fields that no
    longer exist in Bitwarden are deleted, so a revoked or rotated secret is
    not carried over on every re-export.
    """
    if remove_stale:
        retained = {
            BITWARDEN_ID_PROPERTY,
            TOTP_SEED_PROPERTY,
            TOTP_SETTINGS_PROPERTY,
        } | {field["name"] for field in bw_item.get_custom_fields()}
        for name in list(entry.custom_properties):
            if name in retained or _is_url_property_name(name):
                continue
            with contextlib.suppress(AttributeError):
                entry.delete_custom_property(name)
    for field in bw_item.get_custom_fields():
        entry.set_custom_property(
            field["name"],
            field["value"],
            protect=field["type"] == CustomFieldType.HIDDEN,
        )


def _sync_attachments(
    kp: PyKeePass,
    bw: BwClient,
    entry: Entry,
    bw_item: Item,
) -> None:
    """Mirror the item's attachments onto *entry*.

    Attachments removed (or renamed) in Bitwarden are deleted from the entry
    and attachments already present from a previous export are not re-added,
    so repeated exports neither keep stale copies nor accumulate duplicates.
    """
    wanted = bw_item.get_attachments()
    wanted_names = {attachment.get("fileName", "") for attachment in wanted}
    for attachment in entry.attachments:
        if attachment.filename not in wanted_names:
            entry.delete_attachment(attachment)
    present_names = {attachment.filename for attachment in entry.attachments}
    for attachment in wanted:
        if attachment.get("fileName", "") in present_names:
            continue
        attachment_raw = bw.get_attachment(bw_item.get_id(), attachment["id"])
        attachment_id = kp.add_binary(attachment_raw)
        entry.add_attachment(attachment_id, attachment["fileName"])


def bitwarden_to_keepass(args: Namespace, client: BwClient | None = None) -> None:
    try:
        kp = PyKeePass(
            args.database_path,
            password=args.database_password,
            keyfile=args.database_keyfile,
        )
    except FileNotFoundError:
        logger.info("KeePass database does not exist, creating a new one.")
        # Make sure the destination directory exists; otherwise the fresh
        # database cannot be written and the user gets a confusing traceback.
        Path(args.database_path).parent.mkdir(parents=True, exist_ok=True)
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

    # Map every previously exported Bitwarden id to its KeePass entry once,
    # instead of scanning all entries for every item (O(n^2) on big vaults).
    entries_by_bitwarden_id = {
        entry.get_custom_property(BITWARDEN_ID_PROPERTY): entry for entry in kp.entries
    }

    items = bw.list_items()
    logger.info("Starting to process %d items.", len(items))
    for item in items:
        item_type = item.get("type")
        if item_type in (ItemType.CARD, ItemType.IDENTITY):
            logger.warning(
                "Skipping credit card or identity item %s.",
                item.get("name"),
            )
            continue
        if not isinstance(item_type, int):
            # A malformed record must not abort the whole export.
            logger.warning("Skipping item without a type: %s", item.get("name"))
            continue

        bw_item = Item(item)

        # Repeated exports update the entry created by an earlier run instead
        # of piling up near-duplicates.
        entry = entries_by_bitwarden_id.get(bw_item.get_id())
        created = entry is None
        snapshot = None
        original_group = None

        try:
            if created:
                entry = _add_entry_with_title_fallback(kp, groups_by_id, bw_item)
            else:
                original_group = entry.group
                snapshot = _snapshot_entry(entry)
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

            _sync_custom_fields(entry, bw_item, remove_stale=True)
            _sync_attachments(kp, bw, entry, bw_item)

        except Exception as e:
            # Never leave a half-populated entry behind: a newly created entry
            # is rolled back and an updated one is restored from its pre-update
            # snapshot, so a partially-failed item is never saved.
            _rollback_entry(
                kp,
                entry,
                snapshot,
                original_group,
                item,
            )
            logger.warning(
                "Skipping item named %s due to the following error: %r",
                item.get("name"),
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
