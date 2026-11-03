"""User-confirmed requirements for an analytical run."""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


class ClarificationRequired(ValueError):
    def __init__(self, questions):
        self.questions = questions
        super().__init__("Clarification required: " + "; ".join(
            f"{key}: {question}" for key, question in questions.items()
        ))


@dataclass
class TaskSpec:
    goal: str
    sources: list[str] = field(default_factory=list)
    questions: dict[str, str] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)
    output_columns: dict[str, list[str]] = field(default_factory=dict)

    field_mapping: dict[str, str] = field(default_factory=dict)
    time_range: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, str] = field(default_factory=dict)
    group_by: list[str] = field(default_factory=list)
    cleaning_rules: dict[str, str] = field(default_factory=dict)
    rule_sources: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self):
        def nonempty(value):
            return isinstance(value, str) and bool(value.strip())

        if not nonempty(self.goal):
            raise ValueError("TaskSpec requires a goal")
        if not isinstance(self.sources, list) or not all(map(nonempty, self.sources)):
            raise ValueError("sources must be a list of paths relative to data-root")
        for mapping in (self.questions, self.answers, self.field_mapping, self.time_range, self.metrics, self.cleaning_rules):
            if not isinstance(mapping, dict) or not all(
                nonempty(key) and nonempty(value) for key, value in mapping.items()
            ):
                raise ValueError("TaskSpec text mappings require nonempty keys and values")
        if self.answers.keys() - self.questions.keys():
            raise ValueError("unknown answer ID")
        if not isinstance(self.output_columns, dict) or not all(
            nonempty(name) and isinstance(columns, list) and columns
            and all(map(nonempty, columns)) and len(set(columns)) == len(columns)
            for name, columns in self.output_columns.items()
        ):
            raise ValueError("output_columns must map artifacts to unique column names")

        if not isinstance(self.group_by, list) or not all(map(nonempty, self.group_by)):
            raise ValueError("group_by must be a list of nonempty field names")
        if not isinstance(self.rule_sources, dict):
            raise ValueError("rule_sources must map rule IDs to source locations")
        for key, ref in self.rule_sources.items():
            if (not nonempty(key) or not isinstance(ref, dict)
                    or set(ref) != {"source", "line"}
                    or ref["source"] not in self.sources
                    or type(ref["line"]) is not int or ref["line"] < 1):
                raise ValueError("rule source requires a listed document and a positive line number")

    def rule_citations(self, root):
        citations = []
        for rule_id, ref in self.rule_sources.items():
            path = (Path(root) / ref["source"]).resolve()
            if path.suffix.lower() not in (".md", ".txt"):
                raise ValueError("rule source must be Markdown or TXT")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            number = ref["line"]
            if number > len(lines) or not lines[number - 1].strip():
                raise ValueError(f"rule {rule_id!r} points to a missing or blank line")
            citations.append({"rule": rule_id, **ref, "quote": lines[number - 1].strip()})
        return citations

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or "goal" not in data or data.keys() - cls.__dataclass_fields__.keys():
            raise ValueError("invalid TaskSpec fields")
        return cls(**data)

    def require_answers(self):
        pending = {key: question for key, question in self.questions.items() if key not in self.answers}
        if pending:
            raise ClarificationRequired(pending)

    def to_dict(self):
        return asdict(self)
