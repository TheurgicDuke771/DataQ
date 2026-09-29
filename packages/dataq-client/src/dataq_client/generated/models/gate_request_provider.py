from enum import StrEnum


class GateRequestProvider(StrEnum):
    ADF = "adf"
    AIRFLOW = "airflow"
    DBT = "dbt"

    def __str__(self) -> str:
        return str(self.value)
