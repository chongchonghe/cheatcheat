#!/usr/bin/env python3
import argparse
import os
import sys
import shutil
import subprocess
import glob
import re

try:
    import yaml
except ImportError:
    print("Error: PyYAML is not installed. Please install it using 'pip install PyYAML'.")
    sys.exit(1)

def load_config(config_path):
    """Loads configuration from yaml file."""
    if not os.path.exists(config_path):
        print(f"Configuration file not found: {config_path}")
        sys.exit(1)
    
    with open(config_path, 'r') as f:
        try:
            return yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Error parsing config file: {e}")
            sys.exit(1)

def get_cheatpaths(config):
    """
    Returns a list of cheatpaths from config, ensuring local .cheat dirs are included.
    Returns list of dicts: {'name': str, 'path': str, 'readonly': bool, 'tags': list}
    """
    paths = []
    
    # 1. Configured paths
    if 'cheatpaths' in config:
        for cp in config['cheatpaths']:
            expanded_path = os.path.expanduser(cp['path'])
            path_entry = cp.copy()
            path_entry['path'] = expanded_path
            paths.append(path_entry)
            
    # 2. Local .cheat directory (highest priority, so maybe prepend? README says "append ... to the cheatpath" 
    # but also "will be treated as the most local ... and will override less local". 
    # Usually "override" means it is checked *first*.
    # The README says: "The most global cheatpath is listed first in this file; the most local is listed last."
    # "For example, if there is a 'tar' cheatsheet on both global and local paths, you'll be presented with the local one by default."
    # So we should iterate in reverse order to find the "winner", or iterate in priority order (local first) to find the first match.
    # Let's construct a list where the *last* element has the *highest* priority, matching `conf.yml` structure description.
    
    local_cheat = os.path.join(os.getcwd(), '.cheat')
    if os.path.isdir(local_cheat):
        paths.append({
            'name': 'local',
            'path': local_cheat,
            'readonly': False,
            'tags': ['local']
        })
        
    return paths

def find_cheatsheet(cheatname, paths):
    """
    Finds a cheatsheet by name. 
    Searches from most local (last in list) to most global (first in list).
    Returns (path_entry, full_path) or (None, None).
    """
    # Iterate backwards (most local first)
    for path_entry in reversed(paths):
        base_dir = path_entry['path']
        # Check for exact match or match with extension
        # We need to handle subdirectories too, e.g. cheatname = "foo/bar"
        
        # Security check: prevent breakout
        target_path = os.path.join(base_dir, cheatname)
        if not os.path.abspath(target_path).startswith(os.path.abspath(base_dir)):
            continue
            
        # Check file exact
        if os.path.isfile(target_path):
            return path_entry, target_path
            
        # Check file with any extension? README says "file is named 'bar' or 'bar.EXTENSION'"
        # glob for target_path.*
        matches = glob.glob(target_path + ".*")
        if matches:
            # Pick the first one?
            return path_entry, matches[0]
            
    return None, None

def list_cheatsheets(paths, filter_path_name=None):
    """Lists all cheatsheets."""
    sheets = set()
    
    # Iterate all paths
    for entry in paths:
        if filter_path_name and entry.get('name') != filter_path_name:
            continue
            
        base_dir = entry['path']
        if not os.path.isdir(base_dir):
            continue
            
        # Walk directory
        for root, dirs, files in os.walk(base_dir):
            for file in files:
                if file.startswith('.'): continue # ignore hidden files
                
                # Rel path from base_dir
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, base_dir)
                
                # Remove extension for display?
                # README says: named "tar" or "tar.EXTENSION". 
                # If we have tar.md, we display tar.
                base, ext = os.path.splitext(rel_path)
                # If there are multiple extensions or weird dots, this might be tricky.
                # But simple logic: if it's a file, it's a cheat.
                
                # Check if it matches the pattern
                sheets.add(base)
                
    # Sort and print
    for sheet in sorted(sheets):
        print(sheet)

def search_cheatsheets(term, paths):
    """Searches for term in all cheatsheets."""
    found = False
    # Walk all paths
    for entry in paths:
        base_dir = entry['path']
        if not os.path.isdir(base_dir):
            continue
            
        for root, dirs, files in os.walk(base_dir):
            for file in files:
                if file.startswith('.'): continue
                abs_path = os.path.join(root, file)
                
                try:
                    with open(abs_path, 'r', errors='ignore') as f:
                        lines = f.readlines()
                        for i, line in enumerate(lines):
                            if term.lower() in line.lower():
                                # Calculate sheet name
                                rel_path = os.path.relpath(abs_path, base_dir)
                                sheet_name, _ = os.path.splitext(rel_path)
                                print(f"{sheet_name}:{i+1}: {line.strip()}")
                                found = True
                except Exception as e:
                    # Ignore read errors
                    pass
    return found

def edit_cheatsheet(cheatname, paths, config):
    """Edits a cheatsheet. Handles copy-on-write."""
    # check if it exists
    path_entry, full_path = find_cheatsheet(cheatname, paths)
    
    editor = config.get('editor', os.environ.get('EDITOR', 'vi'))
    
    if path_entry:
        # Exists
        if not path_entry.get('readonly', False):
            # Writable, just open
            subprocess.call(f"{editor} '{full_path}'", shell=True)
        else:
            # Read-only, need to copy to first writable path
            print(f"Path '{path_entry['name']}' is read-only. Copying to personal path...")
            
            # Find first writable path (checking from end or start? "first writeable directory in 'cheatpaths'")
            # "cheat will transparently copy that sheet to the first writeable directory in 'cheatpaths'"
            # Assuming 'first' means the first one listed in config (Global?) Or the most local one?
            # "Most global ... listed first ... most local listed last"
            # If I want to override a global one, I should write to a local one.
            # Usually users want to write to their 'personal' path which is usually defined later or is just writable.
            # Let's find the *last* writable path (most local) to ensure it overrides? 
            # Or the *first* writable path found in the list?
            # The README says "first writeable directory". Let's assume list order.
            
            target_entry = None
            for entry in paths: # Iterate in priority order (Global -> Local)
                if not entry.get('readonly', False):
                    target_entry = entry
                    break # Use the first one we find? Or looking for a specific one?
                    # If I have global (RO), work (RW), personal (RW). copy to work or personal?
                    # "First writeable" in list order suggests 'work' in that example.
            
            if not target_entry:
                print("Error: No writable cheatpath found.")
                sys.exit(1)
                
            # Construct new path
            # We assume cheatsheet name might have subdirs
            new_full_path = os.path.join(target_entry['path'], cheatname)
            
            # If extension was present in original, try to preserve it?
            # get extension from full_path
            _, ext = os.path.splitext(full_path)
            if ext and not new_full_path.endswith(ext):
                new_full_path += ext
                
            # Ensure dir exists
            os.makedirs(os.path.dirname(new_full_path), exist_ok=True)
            
            # Copy
            shutil.copy2(full_path, new_full_path)
            print(f"Copied to {new_full_path}")
            
            # Open
            subprocess.call(f"{editor} '{new_full_path}'", shell=True)
            
    else:
        # Does not exist. Create new.
        # Find first writable path
        target_entry = None
        for entry in paths:
             if not entry.get('readonly', False):
                target_entry = entry
                break
        
        if not target_entry:
            print("Error: No writable cheatpath found.")
            sys.exit(1)

        new_full_path = os.path.join(target_entry['path'], cheatname)
        # Verify if we should add extension? For now default to no extension or just as is.
        
        os.makedirs(os.path.dirname(new_full_path), exist_ok=True)
        # Open editor (editor will create file usually, or we touch it)
        # subprocess.call will open it.
        subprocess.call(f"{editor} '{new_full_path}'", shell=True)
        

def view_cheatsheet(cheatname, paths, config):
    """View a cheatsheet."""
    path_entry, full_path = find_cheatsheet(cheatname, paths)
    
    if not path_entry:
        print(f"No cheatsheet found for '{cheatname}'.")
        # Optional: Print "Did you mean...?"
        sys.exit(1)
        
    viewer = config.get('viewer', os.environ.get('PAGER', 'less'))
    
    # Run viewer
    try:
        subprocess.call(f"{viewer} '{full_path}'", shell=True)
    except Exception as e:
        print(f"Error opening viewer: {e}")
        # Fallback to cat
        with open(full_path, 'r') as f:
            print(f.read())

def main():
    parser = argparse.ArgumentParser(description="Create and view interactive cheatsheets.")
    parser.add_argument("cheatname", nargs="?", help="The name of the cheatsheet to view/edit.")
    parser.add_argument("-e", "--edit", action="store_true", help="Edit a cheatsheet.")
    parser.add_argument("-l", "--list", action="store_true", help="List all available cheatsheets.")
    parser.add_argument("-p", "--path", help="Filter by cheatpath name (used with -l).")
    parser.add_argument("-s", "--search", help="Search for a term among cheatsheets.")
    parser.add_argument("-d", "--directories", action="store_true", help="List configured cheatpaths.")
    parser.add_argument("--conf", default=os.path.expanduser("~/.config/cheat/conf.yml"), help="Path to config file.")
    
    # We might need to handle the case where conf.yml is in current dir or specific location?
    # User env: /Users/cche/git/ch/conf.yml. I will default to look there for this task if not specified? 
    # Or just rely on the argument. 
    # For this specific task, the User provided @[conf.yml], so it's likely expected to work with that or default user config.
    # I'll default to looking in local dir first for dev purposes if not found in standard locations.
    
    args = parser.parse_args()
    
    # Config resolution
    config_path = args.conf
    # Fallback to local 'conf.yml' if default doesn't exist but local does (for dev context)
    if config_path == os.path.expanduser("~/.config/cheat/conf.yml") and not os.path.exists(config_path):
        if os.path.exists("conf.yml"):
            config_path = "conf.yml"
            
    config = load_config(config_path)
    paths = get_cheatpaths(config)
    
    if args.directories:
        for p in paths:
            print(f"{p['name']}: {p['path']} (readonly: {p.get('readonly', False)})")
        return

    if args.list:
        list_cheatsheets(paths, args.path)
        return

    if args.search:
        search_cheatsheets(args.search, paths)
        return

    if args.edit:
        if not args.cheatname:
            print("Error: Please specify a cheatsheet to edit.")
            sys.exit(1)
        edit_cheatsheet(args.cheatname, paths, config)
        return

    if args.cheatname:
        view_cheatsheet(args.cheatname, paths, config)
        return
        
    # If no args, print help
    parser.print_help()

if __name__ == "__main__":
    main()
