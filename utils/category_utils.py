import json
import re
from pathlib import Path

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader

# Category matching is driven ENTIRELY by files/filters.json (path from
# config.ini [Paths] FILTERS). Patterns are real regular expressions,
# matched case-insensitively against the release name with re.search.
# Within a group, the FIRST matching category wins, so order matters.

config = ConfigLoader().get_config()
FILTERS_PATH = Path(__file__).resolve().parents[1] / config.get('Paths', 'FILTERS', fallback='files/filters.json')


def load_filters():
    with open(FILTERS_PATH) as f:
        return json.load(f)


def _search_any(patterns, name):
    return any(
        pattern.strip() and re.search(pattern, name, re.IGNORECASE)
        for pattern in patterns or []
    )


def determine_category(directory_name, manual_id=None):
    """Determine the category of the release based on its name, using filters.json."""
    filters = load_filters()

    if manual_id is not None:
        categories = json.loads((Path(__file__).resolve().parents[1] / 'files/digitalcore-categories.json').read_text(encoding='utf-8'))
        for category in categories:
            if int(category['id']) == int(manual_id):
                return category['name'], str(int(manual_id))
        # Validate any explicitly configured extra category.
        for filter_config in filters.values():
            for category_info in filter_config.get('categories', []):
                if str(category_info.get('cat_id')) == str(int(manual_id)):
                    print(f"{bcolors.OKGREEN}Manual Override: {category_info['name']} (ID: {manual_id}){bcolors.ENDC}")
                    return category_info['name'], str(int(manual_id))
        raise ValueError(f"Invalid category ID {manual_id}; see {FILTERS_PATH}")

    name = str(directory_name)
    print(f"{bcolors.YELLOW}Find the correct category\n{bcolors.ENDC}")

    for filter_key, filter_config in filters.items():
        group_patterns = filter_config.get('patterns', {})

        if _search_any(group_patterns.get('exclude_patterns'), name):
            continue
        if not _search_any(group_patterns.get('initial'), name):
            continue

        for category_info in filter_config.get('categories', []):
            if _search_any(category_info.get('exclude_patterns'), name):
                continue
            if not category_info.get('patterns') or _search_any(category_info.get('patterns'), name):
                category_name = category_info.get('name', 'Unknown')
                category_id = str(category_info.get('cat_id'))
                print(f"{bcolors.OKGREEN}Found Category: {category_name} (ID: {category_id})\n{bcolors.ENDC}")
                return category_name, category_id

    # Check for an explicit default category before giving up
    for filter_config in filters.values():
        for category_info in filter_config.get('categories', []):
            if category_info.get('default'):
                print(f"{bcolors.WARNING}No pattern matched; using default category: "
                      f"{category_info['name']} (ID: {category_info['cat_id']}){bcolors.ENDC}")
                return category_info['name'], str(category_info['cat_id'])

    raise ValueError(
        'Category is ambiguous: no filters.json pattern matched. '
        'Specify --cat NUMBER or add a pattern in files/filters.json. No upload was made.'
    )