# Copyright (C) 2025 David Němec
from enum import IntEnum
from urllib.parse import parse_qsl, urlsplit


class ItemType(IntEnum):
    LOGIN = 1
    SECURE_NOTE = 2
    CARD = 3
    IDENTITY = 4


class CustomFieldType(IntEnum):
    TEXT = 0
    HIDDEN = 1
    BOOLEAN = 2


class Item:
    def __init__(self, item: dict) -> None:
        self.item = item

    def get_id(self) -> str:
        return self.item.get("id", "")

    def get_name(self) -> str:
        return self.item.get("name", "")

    def get_folder_id(self) -> str | None:
        return self.item.get("folderId")

    def get_username(self) -> str:
        if "login" not in self.item:
            return ""

        if "username" not in self.item["login"]:
            return ""

        return self.item["login"]["username"] or ""

    def get_password(self) -> str:
        if "login" not in self.item:
            return ""

        if "password" not in self.item["login"]:
            return ""

        return self.item["login"]["password"] or ""

    def get_notes(self) -> str:
        return self.item.get("notes", "")

    def get_uris(self) -> list[str]:
        if "login" not in self.item or "uris" not in self.item["login"]:
            return []
        return [
            (uri.get("uri") or "") if isinstance(uri, dict) else ""
            for uri in self.item["login"]["uris"]
        ]

    def get_custom_fields(self) -> list[dict]:
        if not isinstance(self.item.get("fields"), list):
            return []
        fields = []
        for field in self.item["fields"]:
            if not isinstance(field, dict):
                continue
            try:
                field_type = CustomFieldType(field.get("type"))
            except ValueError:
                # Future/unknown field types are treated as hidden so a new
                # "secret-like" type is never exposed in clear text.
                field_type = CustomFieldType.HIDDEN
            fields.append(
                {
                    "name": field.get("name") or "",
                    "value": field.get("value") or "",
                    "type": field_type,
                },
            )
        return fields

    def get_attachments(self) -> list:
        if "attachments" not in self.item:
            return []

        return self.item["attachments"]

    def get_totp(self) -> tuple[str | None, str | None]:
        if "login" not in self.item:
            return None, None

        if "totp" not in self.item["login"]:
            return None, None

        if not self.item["login"]["totp"]:
            return None, None

        params = urlsplit(self.item["login"]["totp"]).query
        params = dict(parse_qsl(params))
        period = params.get("period", 30)
        digits = params.get("digits", 6)
        secret = params.get("secret", self.item["login"]["totp"])

        return secret, f"{period};{digits}"
