from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.suite_target_file_format_type_0 import SuiteTargetFileFormatType0
from ..models.suite_target_strategy_type_0 import SuiteTargetStrategyType0
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suite_sampling import SuiteSampling


T = TypeVar("T", bound="SuiteTarget")


@_attrs_define
class SuiteTarget:
    """Datasource-shaped run target (#215) — which table / flat-file path / Unity
    Catalog name the suite's checks run against. Same shape as the column-profiler
    request; `run_target.resolve_target` validates the right fields per connection
    type (`table` for SQL, `path` for flat files, `catalog` for Unity Catalog).

        Attributes:
            batch (None | str | Unset):
            catalog (None | str | Unset):
            file_format (None | SuiteTargetFileFormatType0 | Unset):
            namespace (None | str | Unset):
            path (None | str | Unset):
            pattern (None | str | Unset):
            prefix (None | str | Unset):
            sampling (None | SuiteSampling | Unset):
            schema (None | str | Unset):
            strategy (None | SuiteTargetStrategyType0 | Unset):
            table (None | str | Unset):
    """

    batch: None | str | Unset = UNSET
    catalog: None | str | Unset = UNSET
    file_format: None | SuiteTargetFileFormatType0 | Unset = UNSET
    namespace: None | str | Unset = UNSET
    path: None | str | Unset = UNSET
    pattern: None | str | Unset = UNSET
    prefix: None | str | Unset = UNSET
    sampling: None | SuiteSampling | Unset = UNSET
    schema: None | str | Unset = UNSET
    strategy: None | SuiteTargetStrategyType0 | Unset = UNSET
    table: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.suite_sampling import SuiteSampling

        batch: None | str | Unset
        if isinstance(self.batch, Unset):
            batch = UNSET
        else:
            batch = self.batch

        catalog: None | str | Unset
        if isinstance(self.catalog, Unset):
            catalog = UNSET
        else:
            catalog = self.catalog

        file_format: None | str | Unset
        if isinstance(self.file_format, Unset):
            file_format = UNSET
        elif isinstance(self.file_format, SuiteTargetFileFormatType0):
            file_format = self.file_format.value
        else:
            file_format = self.file_format

        namespace: None | str | Unset
        if isinstance(self.namespace, Unset):
            namespace = UNSET
        else:
            namespace = self.namespace

        path: None | str | Unset
        if isinstance(self.path, Unset):
            path = UNSET
        else:
            path = self.path

        pattern: None | str | Unset
        if isinstance(self.pattern, Unset):
            pattern = UNSET
        else:
            pattern = self.pattern

        prefix: None | str | Unset
        if isinstance(self.prefix, Unset):
            prefix = UNSET
        else:
            prefix = self.prefix

        sampling: dict[str, Any] | None | Unset
        if isinstance(self.sampling, Unset):
            sampling = UNSET
        elif isinstance(self.sampling, SuiteSampling):
            sampling = self.sampling.to_dict()
        else:
            sampling = self.sampling

        schema: None | str | Unset
        if isinstance(self.schema, Unset):
            schema = UNSET
        else:
            schema = self.schema

        strategy: None | str | Unset
        if isinstance(self.strategy, Unset):
            strategy = UNSET
        elif isinstance(self.strategy, SuiteTargetStrategyType0):
            strategy = self.strategy.value
        else:
            strategy = self.strategy

        table: None | str | Unset
        if isinstance(self.table, Unset):
            table = UNSET
        else:
            table = self.table

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if batch is not UNSET:
            field_dict["batch"] = batch
        if catalog is not UNSET:
            field_dict["catalog"] = catalog
        if file_format is not UNSET:
            field_dict["file_format"] = file_format
        if namespace is not UNSET:
            field_dict["namespace"] = namespace
        if path is not UNSET:
            field_dict["path"] = path
        if pattern is not UNSET:
            field_dict["pattern"] = pattern
        if prefix is not UNSET:
            field_dict["prefix"] = prefix
        if sampling is not UNSET:
            field_dict["sampling"] = sampling
        if schema is not UNSET:
            field_dict["schema"] = schema
        if strategy is not UNSET:
            field_dict["strategy"] = strategy
        if table is not UNSET:
            field_dict["table"] = table

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.suite_sampling import SuiteSampling

        d = dict(src_dict)

        def _parse_batch(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        batch = _parse_batch(d.pop("batch", UNSET))

        def _parse_catalog(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        catalog = _parse_catalog(d.pop("catalog", UNSET))

        def _parse_file_format(data: object) -> None | SuiteTargetFileFormatType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                file_format_type_0 = SuiteTargetFileFormatType0(data)

                return file_format_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SuiteTargetFileFormatType0 | Unset, data)

        file_format = _parse_file_format(d.pop("file_format", UNSET))

        def _parse_namespace(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        namespace = _parse_namespace(d.pop("namespace", UNSET))

        def _parse_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        path = _parse_path(d.pop("path", UNSET))

        def _parse_pattern(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pattern = _parse_pattern(d.pop("pattern", UNSET))

        def _parse_prefix(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        prefix = _parse_prefix(d.pop("prefix", UNSET))

        def _parse_sampling(data: object) -> None | SuiteSampling | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                sampling_type_0 = SuiteSampling.from_dict(data)

                return sampling_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SuiteSampling | Unset, data)

        sampling = _parse_sampling(d.pop("sampling", UNSET))

        def _parse_schema(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        schema = _parse_schema(d.pop("schema", UNSET))

        def _parse_strategy(data: object) -> None | SuiteTargetStrategyType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                strategy_type_0 = SuiteTargetStrategyType0(data)

                return strategy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SuiteTargetStrategyType0 | Unset, data)

        strategy = _parse_strategy(d.pop("strategy", UNSET))

        def _parse_table(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        table = _parse_table(d.pop("table", UNSET))

        suite_target = cls(
            batch=batch,
            catalog=catalog,
            file_format=file_format,
            namespace=namespace,
            path=path,
            pattern=pattern,
            prefix=prefix,
            sampling=sampling,
            schema=schema,
            strategy=strategy,
            table=table,
        )

        return suite_target
