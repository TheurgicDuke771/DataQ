from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="OtpVerify")


@_attrs_define
class OtpVerify:
    """
    Attributes:
        code (str): The 6-digit code from your email
        email (str):
    """

    code: str
    email: str

    def to_dict(self) -> dict[str, Any]:
        code = self.code

        email = self.email

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "code": code,
                "email": email,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        code = d.pop("code")

        email = d.pop("email")

        otp_verify = cls(
            code=code,
            email=email,
        )

        return otp_verify
