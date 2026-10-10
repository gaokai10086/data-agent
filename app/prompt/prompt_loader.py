from pathlib import Path

prompt_path = Path(__file__).parents[2] / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt by its agent-relative path.

    Examples: ``orchestrator/intent_detection`` and
    ``data_agent/generate_sql``. Keeping the namespace in the call site
    prevents prompts belonging to different agents from being mixed again.
    """
    relative_path = Path(name)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Invalid prompt path: {name}")

    file_path = prompt_path / relative_path.with_suffix(".prompt")
    return file_path.read_text(encoding="utf-8")


if __name__ == '__main__':
    print(load_prompt("data_agent/extend_keywords_for_column_recall"))
    prompt = load_prompt("data_agent/extend_keywords_for_column_recall")
    print(prompt)
