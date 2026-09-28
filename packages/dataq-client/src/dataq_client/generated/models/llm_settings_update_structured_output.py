from enum import StrEnum


class LlmSettingsUpdateStructuredOutput(StrEnum):
    NATIVE = "native"
    PROMPT_JSON = "prompt_json"

    def __str__(self) -> str:
        return str(self.value)
