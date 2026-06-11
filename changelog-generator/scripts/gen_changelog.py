#!/usr/bin/env python3
"""Git changelog generator script.

Parses git log, identifies Conventional Commits, groups by type,
and generates a Markdown changelog.

Usage:
    python3 gen_changelog.py [--from REF] [--to REF] [--output FILE]
    python gen_changelog.py [--from REF] [--to REF] [--output FILE]   (Windows)
"""

import argparse
import re
import subprocess
import sys
import datetime


# Conventional Commit types and their display info
CC_TYPES = {
    "feat":     {"label": "Features",       "icon": "✨"},
    "fix":      {"label": "Bug Fixes",      "icon": "🐛"},
    "docs":     {"label": "Documentation",  "icon": "📝"},
    "style":    {"label": "Style",          "icon": "💄"},
    "refactor": {"label": "Refactoring",    "icon": "♻️"},
    "perf":     {"label": "Performance",    "icon": "⚡"},
    "test":     {"label": "Tests",          "icon": "✅"},
    "chore":    {"label": "Chore",          "icon": "🔧"},
    "ci":       {"label": "CI",             "icon": "👷"},
    "build":    {"label": "Build",          "icon": "📦"},
    "revert":   {"label": "Reverts",        "icon": "⏪"},
}

# Order in which sections appear in the changelog
SECTION_ORDER = [
    "feat", "fix", "revert", "docs", "style", "refactor",
    "perf", "test", "build", "ci", "chore",
]

# Regex for Conventional Commits
# Matches: type(scope)!: description
#   type = feat|fix|docs|...
#   scope = optional, in parens
#   ! = optional, marks breaking change
#   description = everything after : and space
CC_PATTERN = re.compile(
    r"^(\w+)(?:\(([^)]+)\))?(!)?:\s+(.+)$"
)

# Breaking change footer pattern
BREAKING_FOOTER_PATTERN = re.compile(
    r"^BREAKING[ -]CHANGE:\s+(.+)$", re.MULTILINE
)


def run_git(args):
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print(f"git error: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        return result.stdout
    except FileNotFoundError:
        print("Error: git is not installed or not in PATH", file=sys.stderr)
        sys.exit(1)


def get_latest_tag():
    """Get the latest git tag, or None if no tags exist."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def parse_commits(log_text):
    """Parse git log output into structured commit objects."""
    commits = []
    # Split by the delimiter we chose (---commit-sep---)
    entries = log_text.split("---commit-sep---")
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        lines = entry.split("\n")
        if not lines:
            continue
        hash_val = lines[0].strip()
        if not hash_val:
            continue
        # First line of message
        subject = lines[1].strip() if len(lines) > 1 else ""
        # Body (remaining lines)
        body = "\n".join(lines[2:]).strip() if len(lines) > 2 else ""

        commit = {
            "hash": hash_val,
            "subject": subject,
            "body": body,
            "type": None,
            "scope": None,
            "breaking": False,
            "description": subject,  # fallback: full subject
            "is_conventional": False,
        }

        # Try to parse as Conventional Commit
        match = CC_PATTERN.match(subject)
        if match:
            cc_type = match.group(1).lower()
            if cc_type in CC_TYPES:
                commit["type"] = cc_type
                commit["scope"] = match.group(2)
                commit["breaking"] = match.group(3) == "!"
                commit["description"] = match.group(4).strip()
                commit["is_conventional"] = True

        # Check body for BREAKING CHANGE footer
        if BREAKING_FOOTER_PATTERN.search(body):
            commit["breaking"] = True

        commits.append(commit)

    return commits


def generate_changelog(commits, from_ref=None, to_ref=None):
    """Generate a Markdown changelog from parsed commits."""
    # Group by type
    grouped = {}
    breaking_commits = []
    other_commits = []

    for commit in commits:
        if commit["breaking"]:
            breaking_commits.append(commit)
        if commit["is_conventional"]:
            cc_type = commit["type"]
            if cc_type not in grouped:
                grouped[cc_type] = []
            grouped[cc_type].append(commit)
        elif not commit["breaking"]:
            other_commits.append(commit)

    # Build markdown
    lines = []
    lines.append("# Changelog")
    lines.append("")

    # Version header
    today = datetime.date.today().isoformat()
    version_label = to_ref if to_ref else "Unreleased"
    range_info = ""
    if from_ref:
        range_info = f" (from {from_ref})"
    lines.append(f"## {version_label} ({today}){range_info}")
    lines.append("")

    # Breaking changes section (if any)
    if breaking_commits:
        lines.append("### 💥 Breaking Changes")
        lines.append("")
        for commit in breaking_commits:
            scope_str = f"**{commit['scope']}**: " if commit["scope"] else ""
            hash_short = commit["hash"][:7]
            lines.append(f"- {scope_str}{commit['description']} (`{hash_short}`)")
        lines.append("")

    # Sections by type
    for cc_type in SECTION_ORDER:
        if cc_type in grouped and grouped[cc_type]:
            info = CC_TYPES[cc_type]
            lines.append(f"### {info['icon']} {info['label']}")
            lines.append("")
            for commit in grouped[cc_type]:
                # Skip breaking changes here (already listed above)
                if commit["breaking"]:
                    continue
                scope_str = f"**{commit['scope']}**: " if commit["scope"] else ""
                hash_short = commit["hash"][:7]
                lines.append(f"- {scope_str}{commit['description']} (`{hash_short}`)")
            lines.append("")

    # Other (non-conventional) commits
    if other_commits:
        lines.append("### 🔀 Other Changes")
        lines.append("")
        for commit in other_commits:
            hash_short = commit["hash"][:7]
            lines.append(f"- {commit['subject']} (`{hash_short}`)")
        lines.append("")

    # Summary
    total = len(commits)
    conventional = sum(1 for c in commits if c["is_conventional"])
    breaking = len(breaking_commits)
    lines.append("---")
    lines.append("")
    lines.append(f"**{total} commits** | {conventional} conventional | {breaking} breaking change(s)")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a changelog from git history"
    )
    parser.add_argument(
        "--from", dest="from_ref", default=None,
        help="Starting ref (tag, branch, commit hash). Default: latest tag"
    )
    parser.add_argument(
        "--to", dest="to_ref", default=None,
        help="Ending ref (tag, branch, commit hash). Default: HEAD"
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output file path. Default: stdout"
    )

    args = parser.parse_args()

    # Determine range
    from_ref = args.from_ref
    to_ref = args.to_ref or "HEAD"

    if not from_ref:
        from_ref = get_latest_tag()
        if not from_ref:
            # No tags, just get recent commits
            from_ref = None

    # Build git log command
    if from_ref:
        log_range = f"{from_ref}..{to_ref}"
    else:
        log_range = to_ref

    log_output = run_git([
        "log", log_range,
        "--pretty=format:%H%n%s%n%b---commit-sep---",
    ])

    if not log_output.strip():
        print("No commits found in the specified range.", file=sys.stderr)
        sys.exit(0)

    commits = parse_commits(log_output)
    changelog = generate_changelog(commits, from_ref=from_ref, to_ref=to_ref)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(changelog)
        print(f"Changelog written to {args.output}", file=sys.stderr)
    else:
        print(changelog)


if __name__ == "__main__":
    main()
