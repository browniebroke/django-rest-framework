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


def add_github_profile_links(content: str) -> str:
    """
    Convert @username mentions to GitHub profile links.
    
    Skips usernames that are already inside markdown links or are code decorators.
    """
    lines = content.split('\n')
    result_lines = []
    
    for line in lines:
        # Find all existing markdown links to track positions
        link_pattern = r'\[[^\]]+\]\([^)]+\)'
        existing_links = list(re.finditer(link_pattern, line))
        
        # Build a list of character ranges that are inside links
        link_ranges = [(m.start(), m.end()) for m in existing_links]
        
        # Find all @username mentions
        # Pattern: @ followed by word chars, hyphens, or [bot]
        username_pattern = r'@([\w-]+(?:\[bot\])?)'
        username_matches = list(re.finditer(username_pattern, line))
        
        # Process matches in reverse order to maintain indices
        processed_line = line
        for match in reversed(username_matches):
            start, end = match.span()
            username = match.group(1)
            
            # Check if this username is inside an existing link
            is_inside_link = any(link_start <= start < link_end 
                                for link_start, link_end in link_ranges)
            
            # Skip if it's a code decorator (followed by parentheses immediately)
            # e.g., @versioning_class() should not be converted
            if end < len(line) and line[end] == '(':
                continue
            
            if not is_inside_link:
                # Handle bot usernames properly - remove [bot] from URL
                url_username = username.replace('[bot]', '[bot]')
                # Replace @username with [@username](https://github.com/username)
                replacement = f'[@{username}](https://github.com/{url_username})'
                processed_line = processed_line[:start] + replacement + processed_line[end:]
        
        result_lines.append(processed_line)
    
    return '\n'.join(result_lines)


def add_pull_request_links(content: str) -> str:
    """
    Convert pull request URLs to markdown links in format [#NNNN](url).
    
    Skips URLs that are already inside markdown links.
    """
    lines = content.split('\n')
    result_lines = []
    
    # Pattern to match GitHub pull request URLs
    # Matches: https://github.com/encode/django-rest-framework/pull/9757
    pr_url_pattern = r'https://github\.com/([\w\-]+)/([\w\-]+)/pull/(\d+)'
    
    for line in lines:
        # Find all existing markdown links to track positions
        link_pattern = r'\[[^\]]+\]\([^)]+\)'
        existing_links = list(re.finditer(link_pattern, line))
        
        # Build a list of character ranges that are inside links
        link_ranges = [(m.start(), m.end()) for m in existing_links]
        
        # Find all pull request URLs
        pr_matches = list(re.finditer(pr_url_pattern, line))
        
        # Process matches in reverse order to maintain indices
        processed_line = line
        for match in reversed(pr_matches):
            start, end = match.span()
            full_url = match.group(0)
            pr_number = match.group(3)
            
            # Check if this URL is inside an existing link
            is_inside_link = any(link_start <= start < link_end 
                                for link_start, link_end in link_ranges)
            
            if not is_inside_link:
                # Replace URL with [#NNNN](url)
                replacement = f'[#{pr_number}]({full_url})'
                processed_line = processed_line[:start] + replacement + processed_line[end:]
        
        result_lines.append(processed_line)
    
    return '\n'.join(result_lines)


def main():
    """Main function to process the release notes file."""
    parser = argparse.ArgumentParser(
        description='Add GitHub URLs to release notes (profile links and PR links)'
    )
    parser.add_argument(
        'input_file',
        help='Input file path'
    )
    parser.add_argument(
        'output_file',
        help='Output file path where the updated content will be written'
    )
    
    args = parser.parse_args()
    
    input_path = Path(args.input_file)
    output_path = Path(args.output_file)
    
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)
    
    # Read the input file
    content = input_path.read_text()
    
    # Process the content
    # First add pull request links, then profile links
    # (Order matters: we want to process PR URLs before they might be inside links)
    updated_content = add_pull_request_links(content)
    updated_content = add_github_profile_links(updated_content)
    
    # Write to output file
    output_path.write_text(updated_content)

    print(f"Successfully processed {input_path}")
    print(f"Output written to {output_path}")
    print("GitHub profile links and pull request links have been added.")


if __name__ == '__main__':
    main()
