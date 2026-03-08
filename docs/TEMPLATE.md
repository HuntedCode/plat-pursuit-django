# [System Name]

[One paragraph: what this system does, who uses it, why it exists.]

## Architecture Overview

[2-5 paragraphs explaining the high-level design. Data flow diagrams where helpful.
Key design decisions and WHY they were made. Trade-offs that were considered.]

## File Map

| File | Purpose |
|------|---------|
| `path/to/file.py` | Brief description |

## Data Model

[Models involved, key fields, relationships. Not every field: just the ones
that matter for understanding the system. Include FK relationships to models
in other apps.]

## Key Flows

### [Flow Name]

1. Step one
2. Step two
3. ...

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/example/` | No | Description |

## Integration Points

- [Link to related doc](../category/doc.md): how this system connects to that one

## Gotchas and Pitfalls

- **[Short label]**: Explanation of what breaks and how to avoid it
- ...

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `command_name` | What it does | `python manage.py command_name --flag` |

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `prefix:{id}` | 5m | Description |

## Related Docs

- [Related Doc](../category/doc.md): why it's related
