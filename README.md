# Luna Visia

![Docker required](https://img.shields.io/badge/Docker-required-2496ED?logo=docker&logoColor=white)
![Docker Compose v2](https://img.shields.io/badge/Docker_Compose-v2-2496ED?logo=docker&logoColor=white)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Luna Sentia required](https://img.shields.io/badge/Luna_Sentia-required-111111?logo=ollama&logoColor=white)
![PostgreSQL in Luna Sentia](https://img.shields.io/badge/PostgreSQL-in_Luna_Sentia-4169E1?logo=postgresql&logoColor=white)
![NVIDIA GPU optional](https://img.shields.io/badge/NVIDIA_GPU-optional-76B900?logo=nvidia&logoColor=white)
![Status: usable](https://img.shields.io/badge/Status-usable-2EA043)
## Generate, Manipulate, Search and Store images of your choice using LLMs

> **Work in progress:** Luna Visia is a local-first and desktop-first image store and search under active development.

Luna Visia is an AI-powered, private image library that runs locally. A local
Ollama vision model, served by [Luna Sentia](https://github.com/pranjallk1995/Luna_Sentia), analyzes every upload automatically, creates a concise caption and
searchable tags, and stores that metadata in PostgreSQL. FastMCP makes the indexed
collection available to AI assistants of your choice (ChatGPT here) for fast search and safe image-management
operations without repeatedly opening every original image.

> **Requires Luna Sentia, running and healthy:** Luna Visia keeps its data in Luna Sentia's PostgreSQL (`luna-sentia-db`) and joins Sentia's `luna_sentia_network`; it has no database of its own. Start [Luna Sentia](https://github.com/pranjallk1995/Luna_Sentia) first and wait until its `postgres` and `ollama` services report healthy, as described under [Start](#start). Sentia also serves the model: Visia uses `qwen2.5vl:7b` by default, a download larger than 6 GB that needs an NVIDIA GPU with at least 8 GB of VRAM in Sentia's default configuration; see [Vision model](#vision-model) to choose another model.

<p align="center">
  <img src="docs/assets/luna-visia-ai-assistant.png" alt="AI assistant querying Luna Visia alongside its image gallery" width="900">
</p>

The Streamlit interface lets you upload, search, view, favorite, download, hide,
restore, and delete images. Image data, metadata, and AI models persist in Docker volumes,
while OAuth-gated hidden images stay separate from normal galleries and searches.

<p align="center">
  <img src="docs/assets/luna-visia-image-view.png" alt="AI assistant querying Luna Visia alongside its image gallery" width="900">
</p>

An MCP-connected AI assistant can query Luna Visia's filenames, generated captions, and tags,
answer collection-level questions, and return matching filenames without visually
reprocessing every original image.

Luna Visia is also well suited to bulk image manipulation and editing workflows.
Its MCP tools let an AI assistant find and act on groups of images while keeping
the stored files and their searchable metadata synchronized.

The main gallery provides filename search, pagination, AI-generated captions and
tags, full-size viewing, downloads, and deletion controls. Persisted Favorites
provide a focused collection for images you return to often.

Hidden Images uses the same allowlisted Google OAuth identity as the rest of
the UI and supports adding, viewing, downloading, restoring, searching, and
deleting images separately.

Connect Luna Visia's `/mcp` endpoint to the ChatGPT app as a custom MCP server.
Once connected, you can ask ChatGPT to search the vault, inspect image metadata,
display previews, download selected images, and manage the visible collection
through natural-language requests.

<p align="center">
  <img src="docs/assets/chatgpt-mcp-interaction.png" alt="ChatGPT interacting with Luna Visia through MCP" width="760">
</p>

## Current features

### AI-assisted image ingestion

- Upload images through the Streamlit UI or MCP tools.
- Generate a concise caption and normalized searchable tags with the local Ollama vision model.
- Run the vision model and the embedded-provenance check as parallel branches of a LangGraph graph (`mcp/app/services/analysis_graph.py`) that then adds the `ai modified` and `ai info` system tags.
- Preserve the content hash, source size, analysis model, timestamps, and optional public URL in PostgreSQL.
- Replace image content and refresh its metadata as one coordinated operation.
- Backfill missing or stale metadata for files imported outside the normal upload flow.

### Visible image library

- Search filenames, AI captions, and tags without reopening original image files.
- Browse natural-aspect thumbnails with six images per page and full-size viewing.
- Download individual images, or have an LLM package selected originals into a ZIP and return a short-lived download link.
- Rename images safely while keeping filenames, metadata, and generated URLs synchronized.
- Edit captions and tags without rerunning vision analysis.
- Delete image files and their metadata together.

### Favorites and hidden images

- Maintain a persisted Favorites collection for visible images.
- Protect Hidden Images with the allowlisted Google OAuth UI session.
- Authenticate the UI's Hidden Images backend requests with a private service credential.
- View, edit, download, restore, or permanently delete hidden images through authenticated routes.
- Exclude hidden images from public galleries, searches, previews, URLs, and MCP tools.

### Local AI and assistant access

- Run MCP/API and Streamlit as one local Docker Compose stack, using the PostgreSQL, Ollama, and Adminer services from Luna Sentia.
- Expose searchable image operations to compatible assistants through Streamable HTTP MCP.
- Keep Ollama and PostgreSQL private to the shared Docker network while binding UI and MCP ports to localhost.
- Use an optional NVIDIA GPU for vision analysis (configured in Luna Sentia), with a slower CPU fallback.

## Architecture

| Component | Technology | Purpose |
| --- | --- | --- |
| UI | Streamlit | Upload, search, galleries, Favorites, downloads, and authenticated hidden-image workflows |
| HTTP and MCP | FastMCP + Starlette routes | Assistant tools plus UI-facing image, metadata, Favorites, and hidden-vault endpoints |
| Image service | Python + Pillow | Validated ingestion, previews, ZIP delivery, safe file operations, and metadata coordination |
| Vision | Ollama in Luna Sentia | Local caption and tag generation with `qwen2.5vl:7b` by default |
| Database | PostgreSQL 17 in Luna Sentia (`luna-sentia-db`) | Metadata, full-text search indexes, Favorites, hidden state, configuration, and credential hashes |
| Storage | Docker volumes | Original images; the database and Ollama models live in Luna Sentia's volumes |
| Database UI | Adminer in Luna Sentia | Local inspection of every Luna app's database on `luna-sentia-db` |
| Network | `luna_sentia_network` (external) | Shared private network created by Luna Sentia; services use project-specific aliases |

The main service flow is:

```text
Streamlit UI or MCP client
        -> Luna Visia MCP/API
        -> image volume + PostgreSQL metadata
        -> Luna Sentia Ollama (luna-sentia-ollama) when image content changes
```

The UI reads original images from `image_data` as read-only. All mutations go through the MCP/API service so file and database changes remain coordinated.

## Start

Start [Luna Sentia](https://github.com/pranjallk1995/Luna_Sentia) first and wait until it is healthy. Sentia runs
the PostgreSQL server that holds Visia's `lunavisia` database, serves the models,
and creates the shared `luna_sentia_network` every Luna app joins. Visia has no
database of its own: started before Sentia is healthy, it cannot reach its
data and keeps restarting until it can.

```sh
cd ../Luna_Sentia
docker compose up -d --wait postgres ollama adminer
docker compose run --rm postgres-init   # creates the lunavisia role and database
docker compose ps                       # postgres and ollama must show (healthy)
```

See [Is Sentia healthy?](https://github.com/pranjallk1995/Luna_Sentia#is-sentia-healthy) if they do not.

To start Sentia and every Luna app together, with or without Compose Watch, use
`../luna.sh start [--watch]`; see the
[Sentia README](https://github.com/pranjallk1995/Luna_Sentia#start-everything).

Create a private `.env`:

```dotenv
# Must equal LUNAVISIA_DB_PASSWORD in Luna_Sentia/.env
POSTGRES_PASSWORD=replace-with-a-private-database-password
VISION_MODEL=qwen2.5vl:7b
LUNAVISIA_PUBLIC_BASE_URL=
LUNAVISIA_API_TOKEN=replace-with-a-random-value-of-at-least-32-characters
LUNAVISIA_ALLOWED_EMAILS=you@example.com
```

`LUNAVISIA_API_TOKEN` is a shared secret between the UI and the MCP server, not a key issued by any service. Generate your own random value of at least 32 characters:

```sh
openssl rand -hex 32
```

Separate multiple emails with commas. `LUNAVISIA_PUBLIC_BASE_URL` is optional;
`MCP_PUBLIC_URL` is optional and defaults to `http://localhost:18000`; set it to your public HTTPS origin when the server is exposed (plain HTTP is accepted only for localhost).
leave it blank when stable public image URLs are not needed.

### Google OAuth settings

1. Create a Google OAuth 2.0 **Web application** client.
2. Add `http://localhost:18501/oauth2callback`.
3. Copy `ui/.streamlit/secrets.example.toml` to
   `ui/.streamlit/secrets.toml`.
4. Set `redirect_uri`, `cookie_secret`, `client_id`, and `client_secret`.
   Keep `server_metadata_url` unchanged. The redirect value must exactly match
   the URL used to open Visia.

Keep OAuth credentials only in the gitignored secrets file. Do not reuse
secrets. The `.env` allowlist is an additional authorization check after
Google verifies the account.

Start Luna Visia:

```sh
docker compose up --build
```

Open:

- UI: <http://127.0.0.1:18501>
- Service health: <http://127.0.0.1:18000/health>
- Favicon and icons: <http://127.0.0.1:18000/favicon.ico> and `/icons/icon-{48,96,192,512}.png`; the 48 and 96 px icons are also embedded in the MCP `initialize` server info so connector clients such as Claude can show them.
- MCP endpoint: <http://127.0.0.1:18000/mcp>

The example keeps these development defaults:

| Setting | Default |
| --- | --- |
| Database | `lunavisia` |
| Username | `lunavisia` |
| Password | `lunavisia-dev` |
| Database host inside Docker | `luna-sentia-db` |

The API token and Google-account allowlist have no defaults and must be set in
`.env` before Compose starts the application.

Docker is not started automatically by repository setup or validation.

Luna Sentia pulls the vision model; make sure `VISION_MODEL` is listed in
Sentia's `SENTIA_MODELS`. Open Luna Visia at
<http://127.0.0.1:18501>. After signing in with an allowlisted Google account,
the Hidden Images tab opens directly without another password or PIN prompt.

Check GPU use while analysis is active:

```sh
docker compose -f ../Luna_Sentia/docker-compose.yaml exec ollama ollama ps
nvidia-smi
```

If NVIDIA passthrough is unavailable, remove `gpus: all` from the Ollama service in Luna Sentia to use the CPU fallback. Captioning will still work but will be slower.

## Vision model

The default model is `qwen2.5vl:7b`. To use a different Ollama vision model,
add it to `SENTIA_MODELS` in Luna Sentia's `.env`, restart Sentia so it pulls
the model, then set `VISION_MODEL` in Visia's `.env`. For example, use Llama 3.2
Vision 11B:

```dotenv
# Luna_Sentia/.env
SENTIA_MODELS=qwen2.5vl:7b llama3.2-vision:11b

# Luna_Visia/.env
VISION_MODEL=llama3.2-vision:11b
```

Visia reaches Ollama at `http://luna-sentia-ollama:11434` on the shared
`luna_sentia_network`; override it with `OLLAMA_URL` if needed. Ollama is never
published on a host port, and no API key or external image service is used.
Visia does not wait for the model at startup: uploads made before Sentia has
finished pulling it fail until the model is available.

## Codex MCP configuration (optional)

Add the following to the Codex `config.toml` in `.codex` directory in root:

```toml
[mcp_servers.lunavisia]
url = "http://127.0.0.1:18000/mcp"
enabled = true
required = false
startup_timeout_sec = 20
tool_timeout_sec = 60
```

A task that was already open before a new tool was added may need to be reloaded or reopened for tool discovery.

## Database UI

Use [Adminer in Luna Sentia](https://github.com/pranjallk1995/Luna_Sentia#database-ui) at
<http://127.0.0.1:19080>. It reaches every Luna database over the shared private
`luna_sentia_network`; the database port itself remains unavailable from the host. Use:

- System: `PostgreSQL`
- Server: `luna-sentia-db` (the same server holds every Luna app's database)
- Username: `lunavisia`
- Password: your `POSTGRES_PASSWORD`, or `lunavisia-dev` when unset
- Database: `lunavisia`

## MCP tools

Luna Visia exposes these tools to connected MCP clients:

### Browse and retrieve

- `list_images` - list visible images newest first, one page at a time (`limit`, default 50; follow `next_offset`), as compact summaries with each image's `public_url`. `include_preview=true` adds a numbered contact sheet of the first 20 images on the page.
- `view_image` - show a preview of one visible image plus its caption and `public_url`.
- `fetch_images` - display up to 20 visible images in chat as one preview or a numbered contact sheet (a near-square grid at most 1536 px wide, labeled with original filenames, transparency shown on a checkerboard), plus a text key with each tile's number, exact name, caption, and `public_url`; names that are no longer available are listed under `unavailable` instead of failing the request. Or with `delivery="zip"` package up to 200 originals (2 GiB) and return a `download_url` on the public base URL that expires after 24 hours and survives service restarts (archives live on the `luna_visia_download_data` volume, capped at 10 GiB with oldest evicted first). The ZIP is never embedded in the tool result, which keeps large downloads within MCP message limits.
- `get_image_url` - return the stored public URL for one visible image when a base URL is configured.

### Add and modify images

- `upload_image` - add an image from exactly one source: `file` (a chat attachment; declared through ChatGPT's `openai/fileParams`), `image_url` (public HTTPS or data URL), or `content_base64` (small images only; wrapped, unpadded, URL-safe, and data-URL Base64 are accepted). `name` is optional: the extension is corrected to the detected format, taken names get a `_2` suffix, and an exact duplicate of a visible image returns `status: "duplicate"` instead of a second copy.
- `create_upload_link` - return a single-use HTTPS link (15 minutes) that accepts a raw or multipart (`file`) POST. Use it from agents with a shell or code sandbox, since LLMs cannot emit megabytes of Base64.
- `add_image` - add a small image from Base64 (same handling as `upload_image`).
- `modify_image` - replace a visible image from `file`, `image_url`, or `content_base64` and refresh its metadata.
- `rename_image` - safely rename a visible image file and its metadata without re-running vision analysis; its immutable image ID and public URL remain unchanged.
- `rename_images` - rename up to 100 visible images in one call, applied in order so chained renames work. Each rename follows the `rename_image` rules and succeeds or fails on its own; results list `renamed` and `failed` (with each image's error).
- `remove_image` - delete a visible image and its metadata.

### Metadata, search, and collections

- `get_image_metadata` - return everything known about one visible image: the stored record (caption, tags, favorite flag, size, SHA-256, analysis model, timestamps, `public_url`) plus what the file embeds: format, dimensions, readable EXIF (camera, lens, exposure, software, dates, GPS in decimal degrees), PNG text chunks, XMP `DigitalSourceType`/`CreatorTool`, and a summary of its C2PA content-credentials manifest (claim generator, actions, software agent, IPTC digital source type). `ai_generated` and `ai_signals` explain whether embedded provenance marks the image as made by generative AI. C2PA signatures are reported, not verified, and missing provenance does not prove an image is not AI-generated, since re-saving or screenshots strip it.
- `get_gallery_stats` - library statistics computed from stored metadata without opening files: `total_images`, `favorite_count`, `ai_generated_count` (images tagged `ai info`), `ai_modified_count` (images tagged `ai modified`), total and average size (bytes and human-readable), the largest image, a count per file format, `unique_tag_count`, the `top_tags` most used tags (default 10), and the oldest and newest `updated_at`. Hidden images are not counted.
- `list_image_metadata` - bulk metadata for searching and classifying many images with an LLM. It pages newest first like `list_images` (`limit`, default 50; follow `next_offset`) or takes an exact `names` batch, listing missing or hidden names under `unavailable`. `detail="summary"` (up to 100 per call) returns each image's name, caption, tags, favorite flag, size, `updated_at`, and `public_url` plus an `embedded` object with format, dimensions, `ai_generated`, `ai_signals`, generator, camera, lens, capture time, software, and GPS when present. `detail="full"` (up to 20 per call) returns the complete `get_image_metadata` output for each image.
- `update_image_metadata` - update a visible image's caption and/or add or remove tags without replacing the image or re-running vision analysis. Tags must be lowercase: a tag with capital letters is rejected and nothing is changed.
- `update_images_metadata` - apply caption and tag changes to up to 100 visible images in one call, for example LLM classification results from `list_image_metadata`. Each entry (`name`, optional `caption`, `add_tags`, `remove_tags`) is applied independently and reported under `updated` or `failed`; a capitalized tag anywhere rejects the whole batch with nothing changed.
- `set_favorite_images` - add or remove visible images from Favorites using exact filenames.
- `search_images` - search filenames, captions, and tags without opening original images; results include `public_url` and `next_offset`, and `include_preview=true` adds a contact sheet of the first 20 results. Tag filters must be lowercase too.
- `backfill_metadata` - generate missing or stale metadata for imported files, and add the `ai info` tag to already-indexed AI-generated images.

Images whose embedded metadata says generative AI made them get the `ai info` tag automatically on upload or replacement. The signals are a C2PA or XMP IPTC digital source type of `trainedAlgorithmicMedia` or `compositeWithTrainedAlgorithmicMedia` (as in ChatGPT/gpt-image output), a known generator in EXIF `Software` or XMP `CreatorTool`, or diffusion parameters in PNG text chunks. Search for them with `search_images(tags=["ai info"])`. The tag is enforced on every edit path (MCP tools, batch updates, and the web UI, for visible and hidden images): removing `ai info` from an image whose metadata marks it as AI-generated is rejected, and editing such an image that lacks the tag adds it back. On other images the tag can be added or removed freely.

### URL configuration

- `set_public_base_url` - update the optional persisted public base URL and regenerate stored image URLs.

Hidden Images routes are UI-only custom routes protected by the private
`LUNAVISIA_API_TOKEN`; hidden images are not exposed as MCP tools.

## Metadata and search

Every add, upload, or replacement is analyzed before it is committed. The generated caption, normalized tags, content hash, model name, and timestamps are stored in PostgreSQL. Deletion removes the file and its metadata consistently.

The MCP tool:

```text
search_images(
  query: str = "",
  tags: list[str] | None = None,
  match_all_tags: bool = False,
  limit: int = 50,
  offset: int = 0
)
```

returns `{query, normalized_query, count, results[]}`, where each result contains `name`, `caption`, `tags`, and `updated_at`. Search reads the metadata index and never opens original images. PostgreSQL uses a generated weighted `tsvector` with a GIN index for English caption/tag full-text search and a second GIN index for tag array queries.

Run or resume an idempotent metadata backfill through the MCP `backfill_metadata` tool after importing images outside the normal ingestion flow.

## Data model

### `image_metadata`

Stores one row per image: immutable UUID, filename, byte size, SHA-256 content hash, AI caption, normalized tag array, analysis model, creation and update timestamps, hidden state, favorite state, and optional public URL. A weighted PostgreSQL `tsvector` indexes filenames, captions, and tags, with additional indexes for the immutable ID, tags, hashes, hidden images, and Favorites.

The filename remains the storage key matching the image in the persistent `image_data` volume, while external references use the immutable UUID. Public URLs therefore survive filename changes. Legacy filename URLs remain readable for compatibility while that filename still exists. File mutations and metadata updates are coordinated by `ImageVaultService` so a failed operation does not intentionally leave half-applied state.

### `vault_security`

Legacy hidden-vault credential data may remain in upgraded databases for
compatibility, but the OAuth-gated Hidden Images UI no longer reads or updates it.

### `vault_config`

Stores the singleton optional public base URL used to generate immutable-ID image URLs. Updating the origin refreshes each URL without changing its image ID or filename.

The idempotent schema in `mcp/app/data/database.py` runs when the MCP service starts and applies additive upgrades for existing installations.

## Project structure

The backend follows the same layered package layout across all three Luna apps:

- `mcp/app/core/` - configuration, validation models, and domain primitives.
- `mcp/app/data/` - database setup, repositories, and storage adapters.
- `mcp/app/services/` - transport-independent use cases and orchestration.
- `mcp/app/integrations/` - external services, devices, and peer-app clients.
- `mcp/app/transport/` - FastMCP tools, HTTP routes, authentication, and branding.
- `mcp/app/main.py` - the small composition root that wires the layers together.

Luna Vita, Luna Lumia, and Luna Visia share this layout:

```text
.
├── docker-compose.yaml     # services, shared-network membership, and Compose Watch rules
├── README.md
├── UI_STYLE_GUIDE.md       # shared Luna visual reference
├── docs/assets/            # README screenshots
├── mcp/                    # FastMCP server: MCP tools plus UI-facing HTTP routes
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/                # main.py composition root, config, database, models,
│   │                       # repositories, services, mcp_tools, http_routes,
│   │                       # and the app's own domain modules
│   ├── scripts/            # one-off maintenance scripts, when needed
│   └── tests/              # python -m unittest discover -s tests
└── ui/                     # Streamlit interface
    ├── Dockerfile
    ├── requirements.txt
    ├── .streamlit/         # Streamlit theme and server settings
    ├── assets/             # logos
    ├── app.py              # entry point and page composition
    ├── config.py           # environment-backed settings
    ├── styles.py           # shared dark theme
    ├── services/           # backend API client, caching, and session state
    ├── domain/             # Streamlit-free logic and view models, when the app has any
    ├── views/              # Streamlit rendering: tabs, sections, dialogs, shared widgets
    └── tests/              # UI logic tests, when the app has any
```

Dependencies point one way: `views/` uses `services/` and `domain/`, and `domain/`
imports neither Streamlit nor the API client, so it can be tested on its own.

## Development workflow

Use Docker Compose Watch to keep the running development stack aligned with local
source changes:

```sh
docker compose up --build --watch
```

UI source changes are synchronized into `/workspace/ui`, where Streamlit reloads
them automatically. MCP source changes are synchronized into `/workspace/mcp`
and restart only the MCP service so newly registered tools become available from
the server. Changes to either service's `requirements.txt` or `Dockerfile`
rebuild that service. Python caches, test caches, and MCP tests are ignored by the
runtime watcher. Named image and database volumes are never watched or
synchronized.

Compose Watch is a foreground process and must remain running for source sync.
If it exits or its terminal closes, restart it with `docker compose watch`.

If the stack is already running, attach the watcher with `docker compose watch`.
An already-open Codex task may still need to be reopened after an MCP tool-schema
change so the client refreshes its discovered tool catalog.

### UI modules

- `ui/app.py` - `LunaVisiaApp` composition root: header, `GalleryNavigation`, and the upload dialog; each section is a tab class.
- `ui/services/api.py` - `LunaVisiaAPI` keeps backend routes, the private service credential, timeouts, and error text out of the views.
- `ui/config.py` - `UIConfig` reads UI environment settings such as the API URL, API token, allowed Google accounts, image directory, gallery size, and supported image formats.
- `ui/services/state.py` - `SessionState` owns session-state defaults and cross-view transitions such as pending delete/hide and gallery refresh.
- `ui/services/images.py` - `ImageFiles` (vault file listing and cached thumbnails, previews, and size facts) and `ImageCollection` (files plus metadata, search filtering).
- `ui/styles.py` - `LunaStyle` maintains the shared dark visual system, component spacing, and control styling.
- `ui/views/shared.py` - `VisiaContext` (shared dependencies), `VisiaTab` base class, `ResponsiveLayout`, `SearchFilter`, and `ImageLabel`.
- `ui/views/gallery.py` - `ImageGallery`, the grid/list gallery with favorite overlay, item menu, and confirmation dialogs used by every section.
- `ui/views/actions.py` - `GalleryActions`, `FavoriteActions`, and `HiddenActions`: what each section's gallery may do with an image.
- `ui/views/pager.py` - `GalleryPager`, pagination with the animated page transition.
- `ui/views/preview_dialog.py` - `ImagePreviewDialog`, full-size viewing with inline caption and tag editing.
- `ui/views/view_tab.py`, `favorites_tab.py`, `hidden_tab.py` - the three gallery sections; all are rendered only after the shared OAuth gate.
- `ui/views/upload_dialog.py` - `UploadDialog`: upload handling, progress feedback, retries, and newly generated captions and tags.
- `ui/views/__init__.py`, `ui/services/__init__.py` - mark the view and service directories as Python packages.
- `ui/.streamlit/config.toml` - Streamlit theme settings.
- `ui/requirements.txt` - Python dependencies used by the UI container.
- `ui/Dockerfile` - builds the UI container image.

### MCP modules

- `mcp/app/main.py` - `LunaVisiaApplication` composition root: builds the database, repositories, services, and server, then runs it.
- `mcp/app/transport/mcp_tools.py` - `VisiaMCPTools`, the public MCP image-management tools (hidden images are never reachable here).
- `mcp/app/transport/http_routes.py` - `VisiaHTTPRoutes`, the REST routes used by the UI and public image links, with uniform error translation and service-token checks for Hidden Images.
- `mcp/app/services/application.py` - use cases: `VisibleImageService` and `HiddenImageService` (sharing `ImageCollectionService`), `ImageIndexer` (analysis and backfill), `SearchService`, `PublicURLService`, and the `VisiaService` facade.
- `mcp/app/data/repositories.py` - PostgreSQL persistence for image metadata, public URLs, and legacy hidden-vault credentials.
- `mcp/app/data/database.py` - `Database` connection handling and the idempotent schema.
- `mcp/app/data/storage.py` - `ImageStorage`, safe vault paths plus write, rename, and delete operations that roll back if metadata persistence fails.
- `mcp/app/services/media.py` - upload byte validation, chat previews and contact sheets, and `ImageArchive` (uncompressed ZIPs with original filenames).
- `mcp/app/services/downloads.py` - `DownloadStore`, short-lived ZIP archives behind unguessable `/api/downloads/{token}` links.
- `mcp/app/integrations/remote.py` - `ImageSourceResolver` for chat file references, Base64, data URLs, and SSRF-guarded HTTPS upload sources.
- `mcp/app/services/uploads.py` - `UploadLinkStore`, single-use upload tickets behind `/api/uploads/{token}`.
- `mcp/app/core/models.py` - `ImageAnalysis`, `ImageRecord`, `MetadataChange`, and `MetadataRules` (caption, tag, and search-text normalization).
- `mcp/app/integrations/vision.py` - `OllamaVisionAnalyzer`, local vision-model requests.
- `mcp/app/transport/branding.py` - `ServerBranding`, the bundled icon set in `mcp/app/assets/` (favicon plus PNG sizes) exposed as MCP server-info icons and HTTP icon routes.
- `mcp/app/transport/auth.py` - constant-time bearer authentication for OAuth-gated UI requests to Hidden Images routes.
- `mcp/app/transport/security.py` - legacy password/PIN authentication retained only for database compatibility and migration history.
- `mcp/app/core/config.py` - reads and validates MCP settings, including database, image, Ollama, and vision-model configuration.
- `mcp/app/__init__.py` - marks the MCP application directory as a Python package.
- `mcp/tests/` - automated tests for the services, MCP tools, HTTP routes, media helpers, and security safeguards, using in-memory fakes from `fakes.py`.
- `mcp/requirements.txt` - Python dependencies used by the MCP container.
- `mcp/Dockerfile` - builds the MCP container image.

The dark theme is in `ui/.streamlit/config.toml`. Stop the stack with `docker compose down`; named volumes remain.
