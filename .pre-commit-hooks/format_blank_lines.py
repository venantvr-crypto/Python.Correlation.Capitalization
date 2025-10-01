#!/usr/bin/env python3
"""
Pre-commit hook to add blank lines before control structures
and remove blank lines before closing brackets.
"""
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
    """
    Format a Python file by adding and removing blank lines.
    Returns True if the file was modified.
    """
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    if not lines:
        return False

    new_lines = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # --- LOGIQUE DE SUPPRESSION ---
        # Si la ligne actuelle est vide, on regarde la suite.

        if not stripped:
            next_meaningful_line = ""
            # On cherche la prochaine ligne qui n'est pas vide.
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    next_meaningful_line = lines[j].strip()
                    break

            # Si la ligne suivante est un caractère fermant, on saute la ligne vide actuelle.
            if next_meaningful_line.startswith((')', ']', '}')):
                continue  # Ne pas ajouter cette ligne vide, donc la supprimer.

        # --- LOGIQUE D'AJOUT (du script original) ---
        # On ne traite que les lignes avec du contenu pour cette logique.

        if stripped and not stripped.startswith("#"):
            current_indent = len(line) - len(line.lstrip())

            # Ajoute une ligne vide avant les structures de contrôle.
            if needs_blank_line_before(line) and current_indent <= 8:
                if new_lines and new_lines[-1].strip() and not is_control_structure_start(new_lines[-1]):
                    new_lines.append("\n")

        # Ajoute la ligne actuelle (sauf si elle a été sautée par le `continue`).
        new_lines.append(line)

    new_content = "".join(new_lines)

    # On vérifie si le contenu a réellement changé avant d'écrire sur le disque.

    if new_content != content:
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
        print("Formatted files (blank lines adjusted):")

        for f in modified_files:
            print(f"  - {f}")

        return 1  # Signal to git that files were modified

    return 0


if __name__ == "__main__":
    sys.exit(main())
