#!/usr/bin/env python3
"""
fix_table_headers.py - Design system compliance fix
Ensures all <th> elements have: text-xs font-semibold uppercase tracking-wider

Two fixes:
1. Add missing uppercase + tracking-wider to <th> elements that lack them
2. Change font-medium to font-semibold on <th> elements
"""
import os
import re

TEMPLATES_DIR = "/root/dotmac/dotmac_omni/templates"

stats = {
    "files_checked": 0,
    "files_modified": 0,
    "uppercase_added": 0,
    "tracking_added": 0,
    "font_weight_fixed": 0,
    "text_xs_added": 0,
}


def fix_th_classes(class_str):
    """Fix a <th> class string to include required design system classes."""
    changes = []

    # Skip sr-only headers (screen reader only, e.g. "Actions" column)
    if "sr-only" in class_str:
        return class_str, changes

    # Fix 1: font-medium -> font-semibold
    if "font-medium" in class_str and "font-semibold" not in class_str:
        class_str = class_str.replace("font-medium", "font-semibold")
        changes.append("font-medium→font-semibold")

    # Fix 2: Add text-xs if missing
    if not re.search(r"\btext-xs\b", class_str) and not re.search(
        r"\btext-(?:sm|base|lg|xl|2xl)\b", class_str
    ):
        class_str = class_str.rstrip() + " text-xs"
        changes.append("+text-xs")

    # Fix 3: Add font-semibold if no font weight at all
    if not re.search(r"\bfont-(?:medium|semibold|bold)\b", class_str):
        class_str = class_str.rstrip() + " font-semibold"
        changes.append("+font-semibold")

    # Fix 4: Add uppercase if missing
    if "uppercase" not in class_str:
        class_str = class_str.rstrip() + " uppercase"
        changes.append("+uppercase")

    # Fix 5: Add tracking-wider if missing (and no tracking-wide variant exists)
    if not re.search(r"\btracking-wider?\b", class_str):
        class_str = class_str.rstrip() + " tracking-wider"
        changes.append("+tracking-wider")

    # Fix 6: Add text color if completely missing
    if (
        not re.search(r"\btext-slate-[345]\d\d\b", class_str)
        and "text-left" not in class_str.split()
        and not re.search(r"\btext-(?!left|right|center|xs|sm)\S+", class_str)
    ):
        class_str = class_str.rstrip() + " text-slate-500 dark:text-slate-400"
        changes.append("+text-color")

    return class_str, changes


def process_file(filepath):
    """Process a single file, fixing <th> class attributes."""
    with open(filepath) as f:
        content = f.read()

    if "<th" not in content:
        return

    stats["files_checked"] += 1
    original = content

    # Pattern: <th ... class="..."> (including multiline)
    def fix_th_match(match):
        full_match = match.group(0)
        class_content = match.group(1)

        new_class, changes = fix_th_classes(class_content)
        if not changes:
            return full_match

        for c in changes:
            if "uppercase" in c:
                stats["uppercase_added"] += 1
            if "tracking" in c:
                stats["tracking_added"] += 1
            if "font" in c:
                stats["font_weight_fixed"] += 1
            if "text-xs" in c:
                stats["text_xs_added"] += 1

        return full_match.replace(class_content, new_class)

    # Match <th with class attribute (handles both single and multi-line)
    new_content = re.sub(
        r'<th\b[^>]*?class="([^"]*)"',
        fix_th_match,
        content,
        flags=re.DOTALL,
    )

    # Also handle <th without any class attribute - add one
    def add_class_to_bare_th(match):
        th_tag = match.group(0)
        # Skip if already has class
        if 'class="' in th_tag:
            return th_tag
        # Skip if it's a closing tag or self-closing
        if th_tag.startswith("</") or th_tag.endswith("/>"):
            return th_tag

        # Add standard class
        stats["uppercase_added"] += 1
        stats["tracking_added"] += 1
        return th_tag.replace(
            "<th>",
            '<th class="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">',
        )

    new_content = re.sub(r"<th>", add_class_to_bare_th, new_content)

    if new_content != original:
        with open(filepath, "w") as f:
            f.write(new_content)
        stats["files_modified"] += 1
        os.path.relpath(filepath, "/root/dotmac/dotmac_omni")
        # Count changes
        sum(
            1
            for a, b in zip(original.split("\n"), new_content.split("\n"), strict=False)
            if a != b
        )


def main():

    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for fname in sorted(files):
            if fname.endswith(".html"):
                process_file(os.path.join(root, fname))



if __name__ == "__main__":
    main()
