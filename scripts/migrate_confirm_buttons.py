#!/usr/bin/env python3
"""
Migrate inline JS confirm() buttons to danger_button/warning_button macros.

Strategy: find each confirm() occurrence, then walk backward/forward to find
the enclosing <form>...</form> block. Extract action URL, message, label.
Replace entire form block with a macro call.
"""

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Words that indicate warning (amber) vs danger (red)
WARNING_WORDS = {"deactivate", "cancel", "disconnect", "disable", "send"}
DANGER_WORDS = {"delete", "revoke", "remove"}

stats = {"files_modified": 0, "replacements": 0, "imports_added": 0, "skipped": []}


def classify_action(confirm_msg: str, button_label: str, action_url: str) -> str:
    """Determine if this should be danger_button or warning_button."""
    text = (confirm_msg + " " + button_label + " " + action_url).lower()
    for dword in DANGER_WORDS:
        if dword in text:
            return "danger_button"
    for word in WARNING_WORDS:
        if word in text:
            return "warning_button"
    return "danger_button"


def extract_button_label(button_html: str, confirm_msg: str = "") -> str:
    """Extract visible text from button, ignoring SVGs and Jinja2 tags."""
    no_svg = re.sub(r"<svg[\s\S]*?</svg>", "", button_html)
    # Strip Jinja2 tags {{ ... }} and {% ... %}
    no_jinja = re.sub(r"\{\{[^}]*\}\}", "", no_svg)
    no_jinja = re.sub(r"\{%[^%]*%\}", "", no_jinja)
    text = re.sub(r"<[^>]+>", "", no_jinja)
    text = " ".join(text.split()).strip()
    if text:
        return text
    # Fallback: derive from confirm message
    msg_lower = confirm_msg.lower()
    if "deactivate" in msg_lower:
        return "Deactivate"
    if "disconnect" in msg_lower:
        return "Disconnect"
    if "revoke" in msg_lower:
        return "Revoke"
    if "cancel" in msg_lower:
        return "Cancel"
    if "disable" in msg_lower:
        return "Disable"
    return "Delete"


def make_confirm_title(confirm_msg: str, button_label: str) -> str:
    """Generate a short title for the confirmation modal."""
    label = button_label.strip()
    if label.lower() in ("delete", "remove", "deactivate", "cancel", "revoke", "disconnect"):
        return f"Confirm {label.title()}"

    msg_lower = confirm_msg.lower()
    for word in ["delete", "deactivate", "cancel", "revoke", "disconnect", "disable", "send"]:
        if word in msg_lower:
            return f"Confirm {word.title()}"
    return "Confirm Action"


def determine_icon(macro_name: str, confirm_msg: str) -> str:
    """Determine icon type."""
    if macro_name == "warning_button":
        return "warning"
    msg_lower = confirm_msg.lower()
    if "revoke" in msg_lower or "cancel" in msg_lower:
        return "x"
    return "delete"


def determine_size(form_block: str, btn_classes: str) -> str:
    """Determine button size based on context."""
    if 'class="inline' in form_block:
        return "sm"
    if "p-1.5" in btn_classes or "text-xs" in btn_classes:
        return "sm"
    return "md"


def convert_jinja_url(url: str) -> str:
    """Convert action URL with {{ expr }} to Jinja2 ~ concatenation.

    e.g. "/admin/projects/{{ project.id }}/delete"
    ->   "/admin/projects/" ~ project.id ~ "/delete"

    If there are no {{ }}, just returns the quoted string.
    """
    # Find all {{ expr }} in the URL
    jinja_pattern = re.compile(r"\{\{\s*(.+?)\s*\}\}")
    matches = list(jinja_pattern.finditer(url))

    if not matches:
        return f'"{url}"'

    # Split the URL around Jinja2 expressions and build concatenation
    parts = []
    last_end = 0
    for m in matches:
        # Static part before this expression
        static_before = url[last_end:m.start()]
        if static_before:
            parts.append(f'"{static_before}"')

        # The Jinja2 expression (without {{ }})
        expr = m.group(1).strip()
        # Wrap complex expressions (with operators) in parentheses
        if " " in expr:
            parts.append(f"({expr})")
        else:
            parts.append(expr)

        last_end = m.end()

    # Static part after the last expression
    static_after = url[last_end:]
    if static_after:
        parts.append(f'"{static_after}"')

    return " ~ ".join(parts)


def build_macro_call(macro_name, label, title, message, action_url, size, icon, indent):
    """Build the Jinja2 macro call string."""
    message = message.replace('"', '\\"')
    title = title.replace('"', '\\"')

    url_expr = convert_jinja_url(action_url)
    parts = [f'"{label}"', f'"{title}"', f'"{message}"', url_expr]
    if size != "md":
        parts.append(f'size="{size}"')
    if icon != "delete" and macro_name == "danger_button":
        parts.append(f'icon="{icon}"')
    elif icon != "warning" and macro_name == "warning_button":
        parts.append(f'icon="{icon}"')

    args = ", ".join(parts)
    return f"{indent}{{{{ {macro_name}({args}) }}}}"


def find_enclosing_form(content: str, confirm_pos: int):
    """Find the <form> tag that encloses the confirm at confirm_pos.

    Returns (form_start, form_end) positions, or None if not found.
    form_start = start of '<form' tag (including leading whitespace on that line)
    form_end = position after '</form>'
    """
    # Walk backward to find the opening <form tag
    search_back = content[:confirm_pos]

    # Find the last <form before our confirm position
    form_opens = list(re.finditer(r"<form\s", search_back, re.IGNORECASE))
    if not form_opens:
        return None

    form_tag_start = form_opens[-1].start()

    # But we also need to check that there's no </form> between the <form and our confirm
    # (which would mean we're outside that form)
    between = content[form_tag_start:confirm_pos]
    if "</form>" in between.lower():
        return None

    # Find the line start for indentation
    line_start = content.rfind("\n", 0, form_tag_start)
    line_start = line_start + 1 if line_start >= 0 else 0

    # Find the closing </form> after the confirm
    close_match = re.search(r"</form>", content[confirm_pos:], re.IGNORECASE)
    if not close_match:
        return None

    form_end = confirm_pos + close_match.end()

    return (line_start, form_end)


def process_file(filepath: Path) -> bool:
    """Process a single template file."""
    content = filepath.read_text()
    original = content

    if "confirm(" not in content:
        return False

    # Find all confirm() patterns
    confirm_pattern = re.compile(
        r"""(?:onsubmit|onclick)="return confirm\('([^']*)'\);?"\s*"""
    )

    # We need to process from end to start to preserve positions
    matches = list(confirm_pattern.finditer(content))
    if not matches:
        return False

    # Process from last to first to preserve positions
    for m in reversed(matches):
        confirm_msg = m.group(1)
        confirm_pos = m.start()

        # Find enclosing form
        form_bounds = find_enclosing_form(content, confirm_pos)
        if not form_bounds:
            stats["skipped"].append(f"{filepath.name}:{content[:confirm_pos].count(chr(10))+1} - no enclosing form")
            continue

        form_start, form_end = form_bounds
        form_block = content[form_start:form_end]

        # Extract action URL from the form tag
        action_match = re.search(r'action="([^"]*)"', form_block)
        if not action_match:
            stats["skipped"].append(f"{filepath.name} - no action URL")
            continue
        action_url = action_match.group(1)

        # Extract the button content from within the form
        button_match = re.search(
            r"<button[\s\S]*?>([\s\S]*?)</button>", form_block
        )
        if button_match:
            label = extract_button_label(button_match.group(0), confirm_msg)
            btn_class_match = re.search(r'class="([^"]*)"', button_match.group(0))
            btn_classes = btn_class_match.group(1) if btn_class_match else ""
        else:
            label = extract_button_label("", confirm_msg)
            btn_classes = ""

        # Determine parameters
        macro_name = classify_action(confirm_msg, label, action_url)
        size = determine_size(form_block, btn_classes)
        title = make_confirm_title(confirm_msg, label)
        icon = determine_icon(macro_name, confirm_msg)

        # Get indentation
        indent = ""
        for ch in form_block:
            if ch in " \t":
                indent += ch
            else:
                break

        # Build replacement
        replacement = build_macro_call(
            macro_name, label, title, confirm_msg, action_url, size, icon, indent
        )

        # Replace the form block
        content = content[:form_start] + replacement + content[form_end:]
        stats["replacements"] += 1

    if content == original:
        return False

    # Determine which macros are needed
    macros_needed = set()
    if "{{ danger_button(" in content:
        macros_needed.add("danger_button")
    if "{{ warning_button(" in content:
        macros_needed.add("warning_button")

    if macros_needed:
        content = ensure_macro_import(content, macros_needed)

    filepath.write_text(content)
    stats["files_modified"] += 1
    return True


def ensure_macro_import(content: str, macros_needed: set) -> str:
    """Add or update macro import line."""
    if not macros_needed:
        return content

    import_pattern = re.compile(
        r'{%\s*from\s+"components/ui/macros\.html"\s+import\s+([^%]+)%}'
    )
    match = import_pattern.search(content)

    if match:
        existing = {m.strip() for m in match.group(1).split(",")}
        needed = macros_needed - existing
        if not needed:
            return content

        all_imports = sorted(existing | macros_needed)
        new_import = '{%% from "components/ui/macros.html" import %s %%}' % ", ".join(all_imports)
        stats["imports_added"] += 1
        return content[:match.start()] + new_import + content[match.end():]
    else:
        new_import_parts = sorted(macros_needed)
        new_import = '{%% from "components/ui/macros.html" import %s %%}' % ", ".join(new_import_parts)

        extends_match = re.search(r'{%\s*extends\s+[^%]+%}\s*\n', content)
        if extends_match:
            insert_pos = extends_match.end()
            stats["imports_added"] += 1
            return content[:insert_pos] + new_import + "\n" + content[insert_pos:]
        else:
            stats["imports_added"] += 1
            return new_import + "\n" + content


def dry_run_file(filepath: Path):
    """Print what would be replaced without modifying the file."""
    content = filepath.read_text()

    confirm_pattern = re.compile(
        r"""(?:onsubmit|onclick)="return confirm\('([^']*)'\);?"\s*"""
    )
    matches = list(confirm_pattern.finditer(content))
    if not matches:
        return

    rel_path = filepath.relative_to(TEMPLATES_DIR)

    for m in matches:
        confirm_msg = m.group(1)
        confirm_pos = m.start()
        line_num = content[:confirm_pos].count("\n") + 1

        form_bounds = find_enclosing_form(content, confirm_pos)
        if not form_bounds:
            print(f"  {rel_path}:{line_num} - SKIP (no enclosing form)")
            continue

        form_start, form_end = form_bounds
        form_block = content[form_start:form_end]

        action_match = re.search(r'action="([^"]*)"', form_block)
        action_url = action_match.group(1) if action_match else "???"

        button_match = re.search(r"<button[\s\S]*?>([\s\S]*?)</button>", form_block)
        label = extract_button_label(button_match.group(0), confirm_msg) if button_match else extract_button_label("", confirm_msg)

        macro_name = classify_action(confirm_msg, label, action_url)
        size = determine_size(form_block, "")
        title = make_confirm_title(confirm_msg, label)

        print(f"  {rel_path}:{line_num}")
        print(f"    action: {action_url}")
        print(f"    msg:    {confirm_msg}")
        print(f"    label:  {label}")
        print(f"    ->      {macro_name}(\"{label}\", \"{title}\", ..., \"{action_url}\", size=\"{size}\")")
        print()


def main():
    dry = "--dry-run" in sys.argv

    print("=" * 60)
    if dry:
        print("DRY RUN: showing what would be changed")
    else:
        print("Migrating inline confirm() to danger_button/warning_button")
    print("=" * 60)

    template_files = sorted(TEMPLATES_DIR.rglob("*.html"))
    print(f"\nScanning {len(template_files)} template files...\n")

    for filepath in template_files:
        if "macros.html" in filepath.name and "components/ui" in str(filepath):
            continue
        if "confirm_modal.html" in filepath.name:
            continue

        if dry:
            dry_run_file(filepath)
        else:
            before_count = stats["replacements"]
            if process_file(filepath):
                count = stats["replacements"] - before_count
                rel_path = filepath.relative_to(TEMPLATES_DIR)
                print(f"  Modified: {rel_path} ({count} replacements)")

    if not dry:
        print(f"\n{'=' * 60}")
        print(f"Results:")
        print(f"  Files modified:     {stats['files_modified']}")
        print(f"  Total replacements: {stats['replacements']}")
        print(f"  Imports added:      {stats['imports_added']}")
        if stats["skipped"]:
            print(f"\n  Skipped ({len(stats['skipped'])}):")
            for s in stats["skipped"]:
                print(f"    - {s}")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    sys.exit(main())
