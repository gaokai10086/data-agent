from pathlib import Path

prompt_path = Path(__file__).parents[2] / "prompts"


def load_prompt(name: str, *, base_dir: Path | None = None) -> str:
    """Load a prompt by its agent-relative path.

    Examples: ``orchestrator/intent_detection`` and
    ``data_agent/generate_sql``. Keeping the namespace in the call site
    prevents prompts belonging to different agents from being mixed again.

    ``base_dir`` 默认是项目根的 ``prompts/``；传入时从该目录加载，
    用于把提示词与对应 Agent 代码放在一起。
    """
    relative_path = Path(name)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Invalid prompt path: {name}")

    root = base_dir or prompt_path
    file_path = root / relative_path.with_suffix(".prompt")
    return file_path.read_text(encoding="utf-8")


if __name__ == '__main__':
    print(load_prompt("data_agent/extend_keywords_for_column_recall"))
    prompt = load_prompt("data_agent/extend_keywords_for_column_recall")
    print(prompt)
