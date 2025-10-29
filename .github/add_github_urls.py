#!/usr/bin/env python3
"""
Script to add GitHub URLs to release notes.

This script:
1. Converts @username mentions to markdown links pointing to their GitHub profiles
2. Converts pull request URLs to markdown links in format [#NNNN](url)

Existing links are preserved and not modified.
"""

import argparse
import re
import sys
from pathlib import Path


def get_link_ranges(line: str) -> list[tuple[int, int]]:
    """Get character ranges of existing markdown links in a line."""
    pattern = r'\[[^\]]+\]\([^)]+\)'
    return [(m.start(), m.end()) for m in re.finditer(pattern, line)]


def is_inside_link(pos: int, ranges: list[tuple[int, int]]) -> bool:
    """Check if position is inside any of the given ranges."""
    return any(start <= pos < end for start, end in ranges)


def add_github_profile_links(content: str) -> str:
    """Convert @username mentions to GitHub profile links."""
    result = []

    for line in content.split('\n'):
        link_ranges = get_link_ranges(line)
        pattern = r'@([\w-]+(?:\[bot\])?)'
        matches = list(re.finditer(pattern, line))

        processed = line
        for match in reversed(matches):
            start, end = match.span()
            username = match.group(1)

            # Skip if inside link or is a decorator
            if is_inside_link(start, link_ranges) or (end < len(line) and line[end] == '('):
                continue

            url_username = username.replace('[bot]', '[bot]')
            replacement = f'[@{username}](https://github.com/{url_username})'
            processed = processed[:start] + replacement + processed[end:]

        result.append(processed)

    return '\n'.join(result)


def add_pull_request_links(content: str) -> str:
    """Convert pull request URLs to markdown links in format [#NNNN](url)."""
    result = []
    pattern = r'https://github\.com/([\w\-]+)/([\w\-]+)/pull/(\d+)'

    for line in content.split('\n'):
        link_ranges = get_link_ranges(line)
        matches = list(re.finditer(pattern, line))

        processed = line
        for match in reversed(matches):
            start = match.start()

            if is_inside_link(start, link_ranges):
                continue

            replacement = f'[#{match.group(3)}]({match.group(0)})'
            processed = processed[:start] + replacement + processed[match.end():]

        result.append(processed)

    return '\n'.join(result)


def main():
    """Main function to process the release notes file."""
    parser = argparse.ArgumentParser(
        description='Add GitHub URLs to release notes'
    )
    parser.add_argument('input_file', help='Input file path')
    parser.add_argument('output_file', help='Output file path')

    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    content = input_path.read_text()
    content = add_pull_request_links(content)
    content = add_github_profile_links(content)

    Path(args.output_file).write_text(content)
    print(f"Successfully processed {input_path} -> {args.output_file}")


if __name__ == '__main__':
    main()
