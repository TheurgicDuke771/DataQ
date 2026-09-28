from enum import StrEnum


class LlmSettingsUpdateProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"
    SNOWFLAKE_CORTEX = "snowflake_cortex"

    def __str__(self) -> str:
        return str(self.value)
