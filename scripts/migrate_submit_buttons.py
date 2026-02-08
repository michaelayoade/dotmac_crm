#!/usr/bin/env python3
"""
Migrate raw HTML submit buttons to submit_button macro.

Replaces patterns like:
  <button type="submit" class="...bg-primary-600...">Save</button>

With:
  {{ submit_button("Save") }}

Handles colors, sizes, icons, and conditional labels.
"""

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Files/directories to skip
SKIP_PATHS = {
    "auth/",           # Auth pages have custom Alpine.js loading
    "vendor/auth/",    # Vendor auth pages
    "components/ui/macros.html",  # Macro definitions
    "components/overlays/",      # Generic modal component
    "public/",                    # Public pages
}

stats = {"files_modified": 0, "replacements": 0, "imports_added": 0, "skipped": []}


def should_skip_file(filepath: Path) -> bool:
    """Check if file should be skipped."""
    rel = str(filepath.relative_to(TEMPLATES_DIR))
    for skip in SKIP_PATHS:
        if rel.startswith(skip) or skip in rel:
            return True
    return False


def detect_color(classes: str) -> str:
    """Detect button color from CSS classes."""
    if "bg-amber" in classes or "from-amber" in classes:
        return "amber"
    if "bg-emerald" in classes or "from-emerald" in classes:
        return "emerald"
    if "bg-green" in classes or "from-green" in classes:
        return "green"
    if "bg-red" in classes or "from-red" in classes:
        return "red"
    if "bg-blue" in classes or "from-blue" in classes:
        return "blue"
    return "primary"


def detect_size(classes: str) -> str:
    """Detect button size from CSS classes."""
    if "text-xs" in classes or "py-1.5" in classes or "px-3 py-1" in classes:
        return "sm"
    if "text-base" in classes or "py-3" in classes:
        return "lg"
    return "md"


def detect_icon(button_content: str) -> str:
    """Detect icon type from SVG content inside button."""
    if "<svg" not in button_content:
        return "check"  # Default icon

    # Check for common icon paths
    if "M5 13l4 4L19 7" in button_content:
        return "check"
    if "M12 6v6m0 0v6m0-6h6m-6 0H6" in button_content or "M12 4v16m8-8H4" in button_content:
        return "plus"
    # For other SVGs, default to check
    return "check"


def extract_label(button_content: str) -> str:
    """Extract button label text, stripping SVGs and HTML tags but keeping Jinja2."""
    # Remove SVG elements
    no_svg = re.sub(r"<svg[\s\S]*?</svg>", "", button_content)
    # Remove HTML tags but NOT Jinja2 tags
    no_html = re.sub(r"<(?!%|{)[^>]+>", "", no_svg)
    # Clean whitespace
    label = " ".join(no_html.split()).strip()
    return label


def convert_jinja_label(label: str) -> str:
    """Convert a label with {{ expr }} to a Jinja2 expression for macro arg.

    Single expression: {{ 'Update' if x else 'Create' }} -> ('Update' if x else 'Create')
    Mixed: {{ 'Update' if x else 'Create' }} {{ type }} -> ('Update' if x else 'Create') ~ ' ' ~ (type)
    No Jinja2: "Save" -> '"Save"'
    """
    label = label.strip()

    jinja_pattern = re.compile(r"\{\{\s*(.+?)\s*\}\}")
    matches = list(jinja_pattern.finditer(label))

    if not matches:
        return f'"{label}"'

    # Single Jinja2 expression covering the whole label
    if len(matches) == 1 and matches[0].start() == 0 and matches[0].end() == len(label):
        expr = matches[0].group(1).strip()
        return f"({expr})"

    # Mixed text and Jinja2 - build concatenation
    parts = []
    last_end = 0
    for m in matches:
        static_before = label[last_end:m.start()]
        if static_before:
            parts.append(f'"{static_before}"')
        expr = m.group(1).strip()
        if " " in expr:
            parts.append(f"({expr})")
        else:
            parts.append(expr)
        last_end = m.end()

    static_after = label[last_end:]
    if static_after:
        parts.append(f'"{static_after}"')

    return " ~ ".join(parts)


def build_macro_call(label: str, color: str, size: str, icon: str, indent: str, full_width: bool = False) -> str:
    """Build the submit_button macro call."""
    label_expr = convert_jinja_label(label)
    parts = [label_expr]

    # Add non-default parameters
    if color != "primary":
        parts.append(f'color="{color}"')
    if size != "md":
        parts.append(f'size="{size}"')
    if icon != "check":
        parts.append(f'icon="{icon}"')
    if full_width:
        parts.append("full_width=True")

    # Determine loading label based on button label text
    label_lower = label.lower().strip()
    loading = "Saving..."
    for word, lbl in [
        ("create", "Creating..."), ("update", "Updating..."),
        ("add", "Adding..."), ("send", "Sending..."),
        ("post", "Posting..."), ("link", "Linking..."),
        ("connect", "Connecting..."), ("apply", "Applying..."),
        ("import", "Importing..."), ("submit", "Submitting..."),
        ("search", "Loading..."), ("filter", "Loading..."),
        ("generate", "Generating..."), ("schedule", "Scheduling..."),
        ("upload", "Uploading..."), ("convert", "Converting..."),
        ("run", "Running..."), ("enable", "Enabling..."),
        ("disable", "Disabling..."), ("start", "Starting..."),
        ("reply", "Sending..."),
    ]:
        if word in label_lower:
            loading = lbl
            break

    if loading != "Saving...":
        parts.append(f'loading_label="{loading}"')

    args = ", ".join(parts)
    return f"{indent}{{{{ submit_button({args}) }}}}"


def find_button_block(content: str, button_start: int):
    """Find the full button block from <button to </button>.

    Returns (block_start, block_end, full_block) or None.
    block_start includes leading whitespace on the line.
    """
    # Find line start for indentation
    line_start = content.rfind("\n", 0, button_start)
    line_start = line_start + 1 if line_start >= 0 else 0

    # Find closing </button>
    close_match = re.search(r"</button>", content[button_start:])
    if not close_match:
        return None

    block_end = button_start + close_match.end()
    full_block = content[line_start:block_end]

    return (line_start, block_end, full_block)


def should_skip_button(block: str, content: str, pos: int) -> str | None:
    """Check if a button should be skipped. Returns reason or None."""
    # w-full buttons are now supported via full_width parameter

    # Skip buttons with Alpine.js loading state already
    if ":disabled=" in block or "x-show=" in block:
        return "already has Alpine.js state"

    # Skip buttons inside x-data with custom loading
    # Check 10 lines before for x-data
    before = content[max(0, pos - 500):pos]
    if "x-data=" in before and ("loading" in before or "submitting" in before):
        # Check if the x-data is on the form/parent, not a distant element
        last_xdata = before.rfind("x-data=")
        if last_xdata >= 0 and "</form>" not in before[last_xdata:]:
            return "inside x-data with loading state"

    # Skip if button has border-based styling (secondary/outline buttons)
    if "bg-white" in block and "border" in block and "bg-primary" not in block:
        return "secondary/outline button"

    # Skip buttons that are clearly filter/search triggers (not form submissions)
    label = extract_label(block)
    if label.lower() in ("filter", "search", "reset", "clear"):
        return f"filter/search button: {label}"

    return None


def process_file(filepath: Path) -> bool:
    """Process a single template file."""
    content = filepath.read_text()
    original = content

    # Find all <button type="submit" occurrences
    button_pattern = re.compile(r'<button\s+type="submit"')

    matches = list(button_pattern.finditer(content))
    if not matches:
        return False

    # Process from last to first
    for m in reversed(matches):
        button_pos = m.start()

        block_info = find_button_block(content, button_pos)
        if not block_info:
            continue

        block_start, block_end, full_block = block_info

        # Check if should skip
        skip_reason = should_skip_button(full_block, content, button_pos)
        if skip_reason:
            rel = filepath.relative_to(TEMPLATES_DIR)
            line = content[:button_pos].count("\n") + 1
            stats["skipped"].append(f"{rel}:{line} - {skip_reason}")
            continue

        # Extract classes
        class_match = re.search(r'class="([^"]*)"', full_block)
        classes = class_match.group(1) if class_match else ""

        # Must have a primary/colored bg to be a submit button
        has_bg = any(c in classes for c in ["bg-primary", "bg-amber", "bg-emerald",
                                              "bg-green", "bg-red", "bg-blue",
                                              "from-primary", "from-amber", "from-emerald",
                                              "from-green"])
        if not has_bg:
            rel = filepath.relative_to(TEMPLATES_DIR)
            line = content[:button_pos].count("\n") + 1
            stats["skipped"].append(f"{rel}:{line} - no recognized bg color")
            continue

        # Detect parameters
        color = detect_color(classes)
        size = detect_size(classes)
        full_width = "w-full" in classes

        # Extract button content (between > and </button>)
        inner_match = re.search(r">([\s\S]*)</button>$", full_block)
        if not inner_match:
            continue
        inner_content = inner_match.group(1)

        icon = detect_icon(inner_content)
        label = extract_label(inner_content)

        if not label:
            rel = filepath.relative_to(TEMPLATES_DIR)
            line = content[:button_pos].count("\n") + 1
            stats["skipped"].append(f"{rel}:{line} - empty label")
            continue

        # Get indentation
        indent = ""
        for ch in content[block_start:]:
            if ch in " \t":
                indent += ch
            else:
                break

        # Build replacement
        replacement = build_macro_call(label, color, size, icon, indent, full_width)

        # Replace
        content = content[:block_start] + replacement + content[block_end:]
        stats["replacements"] += 1

    if content == original:
        return False

    # Add import
    if "{{ submit_button(" in content:
        content = ensure_macro_import(content, {"submit_button"})

    filepath.write_text(content)
    stats["files_modified"] += 1
    return True


def ensure_macro_import(content: str, macros_needed: set) -> str:
    """Add or update macro import line."""
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
    """Print what would be replaced."""
    content = filepath.read_text()
    button_pattern = re.compile(r'<button\s+type="submit"')
    matches = list(button_pattern.finditer(content))
    if not matches:
        return

    rel = filepath.relative_to(TEMPLATES_DIR)

    for m in matches:
        button_pos = m.start()
        line = content[:button_pos].count("\n") + 1

        block_info = find_button_block(content, button_pos)
        if not block_info:
            continue

        _, _, full_block = block_info

        skip_reason = should_skip_button(full_block, content, button_pos)
        if skip_reason:
            print(f"  {rel}:{line} SKIP: {skip_reason}")
            continue

        class_match = re.search(r'class="([^"]*)"', full_block)
        classes = class_match.group(1) if class_match else ""

        has_bg = any(c in classes for c in ["bg-primary", "bg-amber", "bg-emerald",
                                              "bg-green", "bg-red", "bg-blue",
                                              "from-primary", "from-amber", "from-emerald",
                                              "from-green"])
        if not has_bg:
            print(f"  {rel}:{line} SKIP: no recognized bg color")
            continue

        color = detect_color(classes)
        size = detect_size(classes)
        full_width = "w-full" in classes

        inner_match = re.search(r">([\s\S]*)</button>$", full_block)
        inner_content = inner_match.group(1) if inner_match else ""
        icon = detect_icon(inner_content)
        label = extract_label(inner_content)

        if not label:
            print(f"  {rel}:{line} SKIP: empty label")
            continue

        print(f"  {rel}:{line}")
        print(f"    label:  {label}")
        fw = ", full_width" if full_width else ""
        print(f"    color:  {color}, size: {size}, icon: {icon}{fw}")
        macro = build_macro_call(label, color, size, icon, "    ", full_width)
        print(f"    ->  {macro.strip()}")
        print()


def main():
    dry = "--dry-run" in sys.argv

    print("=" * 60)
    if dry:
        print("DRY RUN: submit button migration preview")
    else:
        print("Migrating raw submit buttons to submit_button macro")
    print("=" * 60)

    template_files = sorted(TEMPLATES_DIR.rglob("*.html"))
    print(f"\nScanning {len(template_files)} template files...\n")

    for filepath in template_files:
        if should_skip_file(filepath):
            continue

        if dry:
            dry_run_file(filepath)
        else:
            before_count = stats["replacements"]
            if process_file(filepath):
                count = stats["replacements"] - before_count
                rel = filepath.relative_to(TEMPLATES_DIR)
                print(f"  Modified: {rel} ({count} replacements)")

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
