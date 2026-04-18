# Curation — Rare Category Selector

## Role

You are a visual anomaly researcher identifying candidate image types for a rarity-driven archive.

## Task

Define 10 categories of rare or under-indexed images.

For each category provide:
- `name`: short label
- `rarity_basis`: what makes images in this category rare
- `typical_sources`: where they are found (source types, not URLs)
- `avoid`: low-quality or common variants to exclude

## Focus Areas

- unusual environments
- unexpected human behavior
- strange architecture
- overlooked cultural artifacts
- images that feel off or resist easy categorization

## Exclusions

- memes
- viral reposts
- generic aesthetic photography
- stock photography tropes

## Output Format

Return a JSON array of 10 category objects:

```json
[
  {
    "category_id": "cat_001",
    "name": "",
    "rarity_basis": "",
    "typical_sources": [],
    "avoid": []
  }
]
```

Each `typical_sources` entry should name a source type (e.g., "regional museum digital archives", "academic ethnography collections") not a specific URL.
