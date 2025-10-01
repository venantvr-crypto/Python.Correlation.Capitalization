#!/usr/bin/env python3
"""Pre-commit hook to add blank lines before/after control structures."""
import sys
from pathlib import Path


def needs_blank_line_before(line: str) -> bool:
    """Check if line needs a blank line before it."""
    stripped = line.lstrip()
    # Only add before if/for/while/try at method/class level, not every occurrence
    return any(
        stripped.startswith(keyword)

        for keyword in ["if ", "for ", "while ", "try:"]

    )


def is_control_structure_start(line: str) -> bool:
    """Check if line starts a control structure."""
    stripped = line.lstrip()
    return any(
        stripped.startswith(keyword)

        for keyword in ["if ", "elif ", "else:", "try:", "except ", "except:", "finally:", "for ", "while ", "with "]

    )


def format_file(filepath: Path) -> bool:
    """Format a Python file by adding blank lines. Returns True if modified."""
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    if not lines:
        return False

    new_lines = []
    modified = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip blank lines and comments

        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue

        current_indent = len(line) - len(line.lstrip())

        # Add blank line before control structures if previous line is not blank
        # and not inside a deeply nested structure

        if needs_blank_line_before(line) and current_indent <= 8:  # Only at top level
            if new_lines and new_lines[-1].strip() and not is_control_structure_start(new_lines[-1]):
                new_lines.append("\n")
                modified = True

        new_lines.append(line)

        # Add blank line after blocks that end (indentation decreases)

        if i + 1 < len(lines):
            next_line = lines[i + 1]
            next_stripped = next_line.strip()

            if not next_stripped:  # Next line is already blank
                continue

            next_indent = len(next_line) - len(next_line.lstrip())

            # Indentation decreased and not a continuation of control structure
            if (
                next_indent < current_indent
                and not is_control_structure_start(next_line)
                and not line.rstrip().endswith("\\")
                and current_indent > 4  # Only for blocks inside methods

            ):
                new_lines.append("\n")
                modified = True

    if modified:
        new_content = "".join(new_lines)
        filepath.write_text(new_content, encoding="utf-8")
        return True

    return False


def main():
    """Main function."""
    modified_files = []

    for filepath_str in sys.argv[1:]:
        filepath = Path(filepath_str)

        if not filepath.suffix == ".py":
            continue

        try:
            if format_file(filepath):
                modified_files.append(filepath)
        except Exception as e:
            print(f"Error processing {filepath}: {e}", file=sys.stderr)
            return 1

    if modified_files:
        print("Formatted files (blank lines added):")

        for f in modified_files:
            print(f"  - {f}")

        return 1  # Signal to git that files were modified

    return 0


if __name__ == "__main__":
    sys.exit(main())
