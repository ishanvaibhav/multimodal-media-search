"""Declarative base shared by every model (plan §11)."""

from __future__ import annotations

import re

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

_naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_naming_convention)


def to_camel(s: str) -> str:
    return re.sub(r"_([a-z])", lambda m: m.group(1).upper(), s)
