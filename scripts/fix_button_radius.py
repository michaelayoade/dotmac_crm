#!/usr/bin/env python3
"""
fix_button_radius.py - Design system compliance fix
Changes rounded-lg to rounded-xl on button elements only.

Rules:
- CHANGE: <button>, <a> styled as buttons (bg-primary, border, bg-red, etc.), <input type=submit>
- KEEP: icon containers (h-N w-N), pagination items, badges, cards, inputs, small icon-only actions (p-2 text-slate-400)
"""
import os
import re

TEMPLATES_DIR = "/root/dotmac/dotmac_omni/templates"

# Patterns that indicate a button-like element (after rounded-lg in same class string)
BUTTON_INDICATORS = [
    r"bg-primary-\d+",
    r"bg-red-\d+",
    r"bg-emerald-\d+",
    r"bg-amber-\d+",
    r"bg-white\b.*?border.*?px-[345]",
    r"border\s+border-slate-\d+.*?px-[345]",
    r"border\s+border-red-\d+",
    r"border\s+border-amber-\d+",
    r"border\s+border-primary-\d+",
    r"font-medium.*?text-white",
    r"font-semibold.*?text-white",
    r"text-white.*?font-medium",
    r"text-white.*?font-semibold",
    r"shadow-sm.*?hover:bg-",
    r"px-[345]\s+py-[12]",
]

# Patterns that indicate NOT a button (icon container, pagination, etc.)
NOT_BUTTON_INDICATORS = [
    r"h-\d+\s+w-\d+\s+.*?rounded-lg",  # icon containers
    r"rounded-lg\s+p-2\s+text-slate",    # small icon-only actions
    r"h-\d+\s+w-\d+\s+items-center\s+justify-center\s+rounded-lg",  # square icon boxes
]

stats = {"files_modified": 0, "replacements": 0, "files_checked": 0}


def is_button_class(class_content):
    """Check if a class string belongs to a button element."""
    # Skip if it matches NOT-button patterns
    for pat in NOT_BUTTON_INDICATORS:
        if re.search(pat, class_content):
            return False

    # Check if it matches button patterns
    return any(re.search(pat, class_content) for pat in BUTTON_INDICATORS)


def process_file(filepath):
    """Process a single file, replacing rounded-lg with rounded-xl on button elements."""
    with open(filepath) as f:
        content = f.read()

    if "rounded-lg" not in content:
        return

    stats["files_checked"] += 1
    original = content
    lines = content.split("\n")
    modified_lines = []
    file_replacements = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        if "rounded-lg" not in line:
            modified_lines.append(line)
            i += 1
            continue

        # Check context: is this line part of a button/a element?
        # Look at surrounding lines for <button or <a context
        context_start = max(0, i - 5)
        context_end = min(len(lines), i + 3)
        "\n".join(lines[context_start:context_end])

        # Strategy 1: Line contains <button or <a with rounded-lg
        is_button_line = bool(re.search(r"<button\b", line)) or bool(
            re.search(r"<a\b.*?(?:bg-primary|bg-red|bg-white.*?border|border\s+border-)", line)
        )

        # Strategy 2: Line is a class= continuation of a button element
        is_button_context = False
        if not is_button_line:
            # Check if recent preceding lines have <button or <a with button styling
            for j in range(i - 1, max(i - 6, -1), -1):
                if j < 0:
                    break
                prev = lines[j]
                if re.search(r"<button\b", prev):
                    is_button_context = True
                    break
                if re.search(r"<a\b.*?(?:bg-primary|bg-red|bg-white.*?border|border\s+border-)", prev):
                    is_button_context = True
                    break
                if re.search(r'class="[^"]*$', prev):
                    # Multi-line class, keep looking up
                    continue
                if re.search(r"<(?:div|span|nav|td|th|label|p|h[1-6]|li|ul|aside|main|header|section|form)\b", prev):
                    break

        # Strategy 3: Check the class content on this line for button indicators
        class_match = re.search(r'class="([^"]*rounded-lg[^"]*)"', line)
        is_button_class_content = False
        if class_match:
            is_button_class_content = is_button_class(class_match.group(1))

        # Also check base_class definition
        is_base_class = "base_class" in line and "rounded-lg" in line

        if is_button_line or is_button_context or is_button_class_content or is_base_class:
            # Additional safety: skip if it's clearly not a button
            if re.search(r"h-\d+\s+w-\d+\s+items-center\s+justify-center\s+rounded-lg", line):
                modified_lines.append(line)
                i += 1
                continue
            if re.search(r"rounded-lg\s+p-2\s+text-slate-4", line):
                modified_lines.append(line)
                i += 1
                continue

            new_line = line.replace("rounded-lg", "rounded-xl")
            if new_line != line:
                file_replacements += 1
            modified_lines.append(new_line)
        else:
            modified_lines.append(line)

        i += 1

    new_content = "\n".join(modified_lines)
    if new_content != original:
        with open(filepath, "w") as f:
            f.write(new_content)
        stats["files_modified"] += 1
        stats["replacements"] += file_replacements
        os.path.relpath(filepath, "/root/dotmac/dotmac_omni")


def main():

    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for fname in sorted(files):
            if fname.endswith(".html"):
                process_file(os.path.join(root, fname))



if __name__ == "__main__":
    main()
