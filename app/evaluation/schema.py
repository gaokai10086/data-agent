"""评测用例的数据结构与 JSONL 读写。"""
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_DATASET = Path(__file__).parent / "golden_questions.jsonl"


@dataclass
class EvalCase:
    id: str
    question: str
    category: str
    gold_sql: str = ""
    expected_result: list = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    metric: str = ""
    difficulty: str = "medium"
    notes: str = ""
    expect_no_sql: bool = False

    @classmethod
    def from_dict(cls, payload: dict) -> "EvalCase":
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in allowed})

    def to_dict(self) -> dict:
        return asdict(self)


def load_cases(path: Path | str = DEFAULT_DATASET) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(EvalCase.from_dict(json.loads(line)))
    return cases


def save_cases(cases: list[EvalCase], path: Path | str = DEFAULT_DATASET) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
