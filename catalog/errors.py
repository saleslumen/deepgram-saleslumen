from pydantic import ValidationError

def format_errors(where: str, exc: ValidationError) -> list[str]:
    lines: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", ()))
        msg = str(error.get("msg", ""))
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        lines.append(f"{where}: {loc}: {msg}")
    return lines

def frontmatter(text: str, where: str, error: type[Exception]) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise error(f"{where}: : must start with YAML frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            header = "\n".join(lines[1:index])
            if header.strip() == "":
                raise error(f"{where}: : frontmatter is incomplete")
            return header, "\n".join(lines[index + 1 :])
    raise error(f"{where}: : frontmatter is incomplete")