# visual-intelligence-bot

A constrained art-interpretation bot that ingests art-related items, produces structured observation records, and writes human-readable notes into Obsidian.

**This repository is separate from `audi_mod` and all automotive tooling. Do not merge concerns.**

---

## Purpose

Increase visual intelligence over time by disciplined observation, not by storytelling.

The bot applies a two-pass interpretation pipeline grounded in:

- `witness` — what is present, directly observable
- `description-vs-inference` — strict separation of literal observation from interpretive claim
- `recurrence` — pattern recognition across the archive, evidence-based only
- `symbolic-candidate` — provisional symbolic readings, never assertions
- `interpretive-restraint` — uncertainty is preserved, not resolved
- `archive-context` — prior context may inform but must not overwrite the image
- `prohibited-inferences` — a defined list of overclaims the system must never make

Concept definitions live in the `visual-intelligence-archive` repo. This repo consumes them.

---

## Repo Boundaries

| Belongs here | Belongs in `visual-intelligence-archive` |
|---|---|
| Ingestion scripts | Concept vocabulary definitions |
| Source records | Canonical schemas |
| Interpretation records | Prompt templates (canonical) |
| Recurrence comparison logic | Eval harnesses |
| Obsidian note writers | MCP resource/prompt packaging |
| Approved source registry | Terminology spec |

---

## Architecture

```
[Approved Source] → ingest/ → source_record.json
                             ↓
                        interpret/
                          PASS 1: literal description
                          PASS 2: constrained interpretation
                             ↓
                    interpretation_record.json
                             ↓
                   recurrence/ → recurrence_check.json
                             ↓
              obsidian_writer/ → .md notes in obsidian/
```

---

## Stack

- **Runtime**: Python 3.11+
- **LLM calls**: provider router (`src/providers/`) — supports Anthropic, OpenAI, xAI, Ollama
- **Storage**: JSON files in `data/records/`, Markdown in `obsidian/`
- **CLI entrypoint**: `scripts/ingest.py`
- **No database in MVP** — file-based, human-auditable

---

## Folder Structure

```
visual-intelligence-bot/
├── README.md
├── .gitignore
├── requirements.txt
├── src/
│   ├── ingest/           # source fetching, source record creation
│   ├── interpret/        # two-pass pipeline
│   ├── recurrence/       # cross-record comparison
│   ├── obsidian_writer/  # markdown note generation
│   └── archive_bridge/   # reads schemas/prompts from archive repo
├── schemas/              # local copies / vendored schemas from archive
├── prompts/
│   ├── pass1/            # literal description prompts
│   ├── pass2/            # constrained interpretation prompts
│   └── recurrence/       # recurrence check prompts
├── obsidian/
│   ├── images/           # one note per ingested image
│   ├── concepts/         # one note per concept (witness, recurrence, etc.)
│   ├── motifs/           # one note per identified motif or recurrence
│   └── sources/          # one note per approved source
├── data/
│   ├── records/          # machine-readable interpretation records (.json)
│   ├── sources/          # approved source registry + source records
│   └── flags/            # human review flags and corrections
└── scripts/
    └── ingest.py         # CLI entrypoint
```

---

## Provider Configuration

Copy `.env.example` to `.env` and fill in at least one provider.

```bash
cp .env.example .env
```

| Provider | Required env var | Notes |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | Default model: `claude-sonnet-4-6` |
| OpenAI | `OPENAI_API_KEY` | Default model: `gpt-4o` |
| xAI / Grok | `XAI_API_KEY` | Default model: `grok-2-vision-1212` |
| Ollama | _(none)_ | Requires Ollama running locally; `ollama pull llava` |

Set `PROVIDER_FALLBACK_ORDER` to control priority (default: `anthropic,openai,xai,ollama`).  
Providers with missing credentials are skipped silently. If all fail, a clear error is raised.

```bash
# Anthropic only
ANTHROPIC_API_KEY=sk-ant-... python scripts/ingest.py ...

# Ollama only (no remote keys needed)
PROVIDER_FALLBACK_ORDER=ollama python scripts/ingest.py ...
```

---

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env   # then add at least one provider key
python scripts/ingest.py --source-url "https://example.com/artwork" --source-id "src_001"
```

---

## MVP Scope

1. Ingest one image URL from an approved source
2. Create a source record
3. Run Pass 1 (literal description)
4. Run Pass 2 (constrained interpretation)
5. Check recurrence against existing records
6. Write one Obsidian note
7. Flag uncertain or overclaiming outputs for human review

**Out of scope for MVP**: RSS ingestion, social posting, dashboards, bulk ingest, auto-publishing.

---

## Governance

- Pass 1 output must contain only observable facts
- Pass 2 output must mark every inference as provisional
- Symbolic candidates must be labeled `[SYMBOLIC-CANDIDATE]`
- Prohibited inferences must be caught before writing any record
- Human review flags must be honored before a record is finalized

---

## Archive Dependency

This repo vendors a snapshot of schemas and prompts from `visual-intelligence-archive`.

To update vendored files:
```bash
python scripts/sync_archive.py --archive-path ../visual-intelligence-archive
```

Schema drift is blocked by validation against `schemas/interpretation_record.schema.json`.
