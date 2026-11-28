"""The import payload models stay field-for-field the export ones (#1610).

`GET /suites/{id}/export` and `POST /suites/import` carry the same document, but only the
request side may reject an unknown key, so each model has a request twin on `ApiRequestModel`.
Nothing else ties the two field lists together: a field added to one side only would export a
document that no longer imports, or the reverse.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import BaseModel

from backend.app.api.v1 import suites

TWINS: list[tuple[type[BaseModel], type[BaseModel]]] = [
    (suites.SourceConnectionRef, suites.SourceConnectionRefIn),
    (suites.CheckDocument, suites.CheckDocumentIn),
    (suites.SuiteDocument, suites.SuiteDocumentIn),
]
# The one difference allowed: a nested model is replaced by its own twin.
NESTED = {inbound: outbound for outbound, inbound in TWINS}


def _shape(model: type[BaseModel]) -> dict[str, tuple[Any, ...]]:
    def plain(annotation: Any) -> str:
        text = repr(annotation)
        for inbound, outbound in NESTED.items():
            text = text.replace(inbound.__qualname__, outbound.__qualname__)
        return text

    return {
        name: (
            plain(field.annotation),
            field.default,
            field.default_factory,
            tuple(repr(m) for m in field.metadata),
            field.alias,
            field.validation_alias,
            field.serialization_alias,
        )
        for name, field in model.model_fields.items()
    } | {"validators": tuple(sorted(model.__pydantic_decorators__.field_validators))}


@pytest.mark.parametrize(("outbound", "inbound"), TWINS, ids=lambda m: m.__name__)
def test_the_request_twin_has_the_same_fields(
    outbound: type[BaseModel], inbound: type[BaseModel]
) -> None:
    assert _shape(inbound) == _shape(outbound)


def test_only_the_request_side_forbids_unknown_keys() -> None:
    for outbound, inbound in TWINS:
        assert inbound.model_config.get("extra") == "forbid", inbound.__name__
        assert outbound.model_config.get("extra") != "forbid", outbound.__name__


def _models_in(annotation: Any) -> set[type[BaseModel]]:
    """Every model class an annotation names, through `X | None`, `list[X]` and the like."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {annotation}
    return {m for arg in get_args(annotation) for m in _models_in(arg)}


@pytest.mark.parametrize(("outbound", "inbound"), TWINS, ids=lambda m: m.__name__)
def test_each_side_nests_only_its_own_side(
    outbound: type[BaseModel], inbound: type[BaseModel]
) -> None:
    """The comparison above maps a nested twin to its counterpart, so on its own it would pass
    an import model that nests a lenient export one, and that part of the payload would then
    drop unknown keys instead of refusing them."""
    exports, imports = set(NESTED.values()), set(NESTED)
    for name, field in inbound.model_fields.items():
        assert not _models_in(field.annotation) & exports, f"{inbound.__name__}.{name}"
    for name, field in outbound.model_fields.items():
        assert not _models_in(field.annotation) & imports, f"{outbound.__name__}.{name}"
