from enum import StrEnum


class CheckDryRunResultMetricValueRedactionType0(StrEnum):
    CLASSIFIER = "classifier"
    DEFAULT_MASK = "default_mask"
    DETECTED = "detected"
    INHERITED_TAG = "inherited_tag"
    OWN_TAG = "own_tag"
    POLICY = "policy"
    UNCLASSIFIED = "unclassified"

    def __str__(self) -> str:
        return str(self.value)
