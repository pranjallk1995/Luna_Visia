# Luna UI style guide

Shared visual reference for every Luna app with a React UI: Visia, Vita, Lumia, Fiora,
Lexia, Vibia, and Unia. Luna Socia keeps a copy for its own pages, which use the same
palette and `lv-` classes. This file is identical in all eight repositories. Any change
goes into all eight copies at the same time.

Luna Visia is the reference implementation. When this guide and the code
disagree, check this guide first, fix the code to match it, and record any
intended exception under [Known deviations](#known-deviations).

## How a Luna UI is built

Each UI is a React and TypeScript app built with Vite, served by a small Starlette
gateway on port 8501. The browser talks only to the gateway.

| Part | Where | Holds |
| --- | --- | --- |
| Styles | `ui/web/src/styles.css` | The palette, type scale, shell, and app-specific rules |
| Sign-in pages and brand | `ui/web/src/App.tsx` in Visia, `ui/web/src/components/Gate.tsx` elsewhere | Logo, title, subtitle, sign-in and refusal pages |
| Shared components | `ui/web/src/components/` | `Toasts`, `Dialog`, and, where used, `ConfirmDialog` and `Pager` |
| Settings | `ui/config.py` | Limits and options; the browser app reads them from `/auth/session` and never hard-codes them |

Shared classes use the `lv-` prefix in every app. Rules for one app's own screens
use its prefix: `lu-` Unia, `ll-` Lumia, `lf-` Fiora, `lx-` Lexia, `vt-` Vita. Vita's
charts and calendar keep their descriptive names (`nutrition-card`, `workout-day`).

## Color palette

| Role | Color | Variable |
| --- | --- | --- |
| Background | `#111111` | `--lv-bg` |
| Surface / panel | `rgba(255, 255, 255, 0.055)` | `--lv-surface` |
| Control | `rgba(255, 255, 255, 0.07)` | `--lv-control` |
| Control hover | `rgba(255, 255, 255, 0.12)` | `--lv-control-hover` |
| Primary text | `#f5f5f5` | `--lv-text` |
| Muted text | `#a3a3a3` | `--lv-muted` |
| Border | `rgba(255, 255, 255, 0.14)` | `--lv-border` |
| Strong border | `rgba(255, 255, 255, 0.34)` | `--lv-strong` |
| Accent | `#f5f5f5` | `--lv-accent` |
| Accent surface | `rgba(255, 255, 255, 0.09)` | `--lv-soft` |

The page background adds a soft glow:
`radial-gradient(circle at 82% -10%, rgba(255, 255, 255, 0.07), transparent 30rem)`
over `--lv-bg`, fixed to the window.

The apps are dark only. The header subtitle uses the muted text color. Titles and
important foreground content use the primary text color.

Status colors are reserved for state, and always come with text or an icon: green
`#4ade80` for success or running, amber `#fbbf24` or `#facc15` for a warning, and rose
`#fb7185` for danger.

## Typography

| Role | Size | Variable |
| --- | --- | --- |
| App title | `clamp(1.65rem, 3vw, 2.15rem)` | `--luna-text-title` |
| Header subtitle | `0.78rem` | `--luna-text-subtitle` |
| Body text | `0.88rem` | `--luna-text-body` |
| Supporting text / labels | `0.72rem` | `--luna-text-small` |

The font is Source Code Pro, self-hosted through
`@fontsource-variable/source-code-pro` and imported in `main.tsx`, so the content
security policy needs no outside font host. The build inlines the font's smallest
files, so the policy needs `font-src 'self' data:`. It falls back to the system
monospace stack:

```css
font-family: "Source Code Pro Variable", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
```

The app title uses weight `720`, letter spacing `-.04em`, and line height `1`. The
subtitle uses weight `400` and the muted color.

## Icons

Icons come from `lucide-react`. Use the component at `size={16}` to `size={18}`, with
an `aria-label` on any button that shows only an icon. Do not load an icon font.

## Logo assets

The page and favicon both use `ui/web/public/logo.png`, a small transparent PNG. The
larger source art stays in `ui/assets/` and is not part of the built app. Never inline
a logo as a `data:` URI.

| App | Served logo | Source art |
| --- | --- | --- |
| Luna Visia | 96 x 96 px | `ui/assets/lunavisia_logo.png` |
| Luna Vita | 96 x 96 px | `ui/assets/lunavita_logo.png` |
| Luna Vibia | 96 x 96 px | `ui/assets/lunavibia_logo.png` |
| Luna Unia | 96 x 96 px | `ui/assets/lunaunia_logo.png` |
| Luna Lumia | 216 x 216 px | `ui/assets/lunalumia_logo.png` |
| Luna Fiora | 216 x 216 px | `ui/assets/lunafiora_logo.png` |
| Luna Lexia | 216 x 216 px | `ui/assets/lunalexia_logo.png` |

The logo shows at `5.75rem x 4.5rem` on desktop and `4rem x 3.4rem` on phones, with
`object-fit: contain`.

## Header

The header is the part of the shell that must look identical across apps. Only the
logo, title, subtitle, and the controls on the right change per app.

### Structure

```tsx
<main className="lv-page">
  <div className="lv-header-row">
    <div className="lv-header">
      <img className="lv-mark" src="/logo.png" alt="Luna <App> logo" />
      <div>
        <h1 className="lv-title">Luna <App></h1>
        <p className="lv-subtitle">Private AI-managed <what the app does></p>
      </div>
    </div>
    <div className="lv-header-actions">{/* controls, ending with Sign out */}</div>
  </div>
  {/* Primary navbar, directly below */}
</main>
```

The subtitle always follows the pattern "Private AI-managed …".

| App | Subtitle ends with |
| --- | --- |
| Luna Visia | image storage |
| Luna Vita | nutrition tracking |
| Luna Lumia | smart lighting |
| Luna Fiora | personal finance |
| Luna Lexia | document storage |
| Luna Vibia | music library |
| Luna Unia | home for every Luna app |

### Right-side controls

| App | Controls, left to right |
| --- | --- |
| Luna Visia | Grid view, List view, **Upload Images** (primary), Sign out |
| Luna Vita | Previous day (icon only), Log date picker, Next day (icon only), Sign out |
| Luna Lumia | Check lights (icon only), **Turn all on / off** (primary), Sign out |
| Luna Fiora | From date, To date, Sign out |
| Luna Lexia | Grid view, List view, **Upload Docs** (primary), Sign out |
| Luna Vibia | Grid view, List view, **Upload Tracks** (primary), Sign out |
| Luna Unia | **Check again** (primary), Sign out |

Shared rules:

- **Sign out** is always last. It is a link to the `logout_url` from `/auth/session`,
  with the class `lv-button lv-sign-out`, `7rem` wide on desktop.
- The app's main action has `lv-button-primary lv-header-main`, and takes the free
  width.
- Every control is at least `2.625rem` tall, has a `.5rem` radius, and does not wrap
  its text.
- Do not move controls with manual offsets or spacer elements.

### Desktop geometry

Use these values unchanged:

```css
.lv-page { max-width: 1180px; margin: 0 auto; padding: 2.35rem 1rem 3rem; }

.lv-header-row {
  display: grid;
  grid-template-columns: 5fr 4fr;
  gap: 1rem;
  align-items: center;
  margin-bottom: .55rem;
}

.lv-header { display: flex; align-items: center; gap: 1rem; min-height: 4.5rem; margin-bottom: 1rem; }
.lv-mark { display: block; flex: 0 0 5.75rem; width: 5.75rem; height: 4.5rem; object-fit: contain; }
.lv-header-actions { display: flex; gap: .5rem; align-items: center; justify-content: flex-end; }
```

Keep the header row's height intrinsic. A fixed height pushes the navbar down. The
header's vertical position comes from the page's `2.35rem` top padding alone: render
nothing above it, and do not fix a position problem with `top`, `transform`, negative
margins, or spacer elements.

### Phones (max-width: 700px)

```css
.lv-page { padding: 1rem 16px 2rem; }
.lv-header-row { grid-template-columns: minmax(0, 1fr); gap: 0; }
.lv-header { gap: .65rem; min-height: 3.4rem; margin-bottom: .65rem; }
.lv-mark { flex: 0 0 4rem; width: 4rem; height: 3.4rem; }
.lv-title { font-size: clamp(1.55rem, 8vw, 1.85rem); white-space: nowrap; }
.lv-subtitle { display: none; }
.lv-sign-out { flex: 0 0 auto; font-size: 0; gap: 0; }
```

The brand stacks above the controls, the brand and the controls each stay on one line,
and Sign out collapses to its icon. The page never scrolls sideways; wide tables
scroll inside `lv-table-scroll`. The navbar moves to the bottom of the screen: see
[Phone layout](#phone-layout).

## Primary navbar

The navbar starts at the same on-screen position in every app at the same viewport
size, zoom, and root font size. It is full width with equal-width items.

```tsx
<nav className="lv-nav" aria-label="Section">
  <button className="lv-nav-item lv-nav-active" aria-current="page" aria-label="Overview">
    <Icon size={17} /><span>Overview</span>
  </button>
</nav>
```

```css
.lv-nav {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: .2rem;
  margin: .2rem 0 1.15rem;
  padding: .4rem;
  border: 1px solid var(--lv-border);
  border-radius: .9rem;
  background: var(--lv-surface);
}
```

Each item is at least `2.55rem` tall with a `.65rem` radius. On phones the same markup
becomes a tab bar along the bottom of the screen, with each icon above its label: see
[Phone layout](#phone-layout).

| App | Sections |
| --- | --- |
| Luna Visia | View Images, Favorite Images, Hidden Images |
| Luna Vita | Daily Tracking, Monthly Tracking, Health Reports |
| Luna Lumia | Home, Schemes, Schedules, PC, Devices |
| Luna Fiora | Overview, Transactions, EMIs & SIPs, Goals & reminders |
| Luna Lexia | View Documents, Favorite Documents, Hidden Documents |
| Luna Vibia | View Tracks, Favorite Tracks, Playlists, Hidden Tracks |
| Luna Unia | None: a single page (see [Known deviations](#known-deviations)) |
| Luna Socia | None: one chat screen (see [Known deviations](#known-deviations)) |

The first section is where people land, so it shows what is happening now and the
everyday controls; setup lives in the last section. See [Everyday screens](#everyday-screens).

The chosen section is remembered in browser storage, through `remember` and
`remembered` in `lib.ts`, and the app must work the same when storage is refused.

Sections within a page, such as Lumia's Library / Solid / Sequence / Transitions, use
the lighter `lv-tabs` underline, never a second navbar.

### States

Labels and icons stay muted in every state. State is shown through the surface and
border only.

- **Default:** control background, transparent border, muted icon and label.
- **Selected:** control-hover background, `1px` strong border, and a `0 0 0 1px`
  border-colour ring.
- **Hover:** control-hover background, strong border, `translateY(-1px)`, and a
  `140ms` transition on border, background, and transform.
- **Focus:** the global `2px` accent outline for keyboard users, with no colour change.

## Phone layout

Phones (`max-width: 700px`) get their own arrangement, not a squeezed desktop: the
things a thumb uses most sit at the bottom of the screen.

- **Tabs along the bottom.** `lv-nav` is fixed to the bottom edge, full width, with a
  top border only and a blurred `rgba(17,17,17,.94)` background. Each item stacks its
  icon above a `.64rem` label that may wrap to two lines; the selected item shows its
  icon on a control-hover pill and its label in primary text. The bar clears the home
  indicator with `env(safe-area-inset-bottom)`, and `lv-page` gets bottom padding so
  nothing hides under it.
- **Panels become bottom sheets.** A desktop side panel (Lumia's control panel) becomes
  a sheet fixed above the tab bar, with rounded top corners. Its head (what it changes,
  with a chevron) toggles the rest; while nothing is selected only the head shows. The
  sheet never covers more than `80vh` and scrolls inside.
- **Everything pinned stays above the bar.** Toasts and selection bars (`lv-bulk`) sit
  `4.6rem` above the bottom edge plus the safe area.
- **Two columns at most.** Status strips use two columns with long cells spanning
  both; tile grids use two columns.
- **Touch targets** stay at least `2.75rem` (44px) in each direction.

Every app with a navbar appends the same block to the end of its `styles.css`, under
`/* Phones: the tabs move to a bar along the bottom */`, so it overrides the desktop
rules:

```css
@media (max-width: 700px) {
  .lv-page { padding-bottom: calc(5.5rem + env(safe-area-inset-bottom)); }
  .lv-nav { position: fixed; z-index: 30; right: 0; bottom: 0; left: 0; gap: .1rem; margin: 0;
    padding: .3rem .35rem calc(.3rem + env(safe-area-inset-bottom)); border-width: 1px 0 0;
    border-radius: 0; background: rgba(17, 17, 17, .94); backdrop-filter: blur(12px); }
  .lv-nav-item, .lv-nav-item:hover { flex-direction: column; gap: .2rem; min-width: 0;
    min-height: 3.2rem; padding: .25rem .1rem; border: 0; background: transparent;
    box-shadow: none; transform: none; }
  .lv-nav-item svg { box-sizing: content-box; padding: .2rem .85rem; border-radius: 999px; }
  .lv-nav-item span { position: static; width: auto; height: auto; clip-path: none;
    max-width: 100%; font-size: .64rem; line-height: 1.15; text-align: center;
    overflow-wrap: anywhere; }
  .lv-nav-active { border: 0; background: transparent; box-shadow: none; color: var(--lv-text); }
  .lv-nav-active svg { background: var(--lv-control-hover); }
  .lv-toasts { inset: auto 1rem calc(4.6rem + env(safe-area-inset-bottom)) 1rem; }
  .lv-bulk { bottom: calc(4.6rem + env(safe-area-inset-bottom)); }
}
```

## Everyday screens

Luna Lumia's Home tab sets the pattern for an app's first section. Apply it wherever an
app has live state and controls; galleries keep [Galleries](#galleries).

- **A Now bar first.** A strip of cells (`grid-template-columns: repeat(auto-fit,
  minmax(15rem, 1fr))`, `1px` gaps over the border color, `#171717` cells) saying what
  is happening: a count with its one main switch, what is running with a **Stop**, what
  happens next, and on/off pills for automations. Each cell is a muted label, a bold
  value, and a muted detail line.
- **Tiles show real state.** Each item is a tile whose look is its state (Lumia's rooms
  glow in their lights' colors and brightness), with its status in words below the name
  and its own quick switch in the corner. Off and offline tiles are flat and muted.
- **Select, then act.** Tapping a tile selects it (strong border, a check in the
  corner); several can be selected, and a chip row selects a saved set (Lumia's
  groups). One control panel changes the whole selection, and with nothing selected it
  says what it changes instead. No slider or menu is repeated on every card.
- **The panel.** On desktop a sticky side panel (`flex: 1 1 20rem; max-width: 26rem`)
  beside a `flex: 999 1 34rem` main column, wrapping under it on narrow windows: what it
  changes and **Clear**, the quick actions (On / Off, brightness), then the rest
  (modes, presets, saved schemes as buttons with a color strip, the items it covers). On
  phones it is the bottom sheet from [Phone layout](#phone-layout).
- **Setup apart from use.** Renaming, pictures, adding and finding devices, and deleting
  live in the last section (Lumia's **Devices**), so no bin icon sits beside an everyday
  control.

## Page selector

Paged lists use `Pager`: a centered row of numbered buttons between previous and next
arrows. Never use a dropdown or any other pop-up to choose a page.

```text
←  1  2  3  4  5  …  17  →      near the start
←  1  …  4  5  6  …  17  →      in the middle
←  1  …  13  14  15  16  17  →  near the end
```

- `pageSlots` in `lib.ts` chooses the numbers. Past seven pages it always gives seven
  slots, so the buttons do not shift while paging. The first, last, and current pages
  are always shown.
- The current page has the strong border, the accent surface, weight `650`, and
  `aria-current="page"`. Other numbers are muted with no border until hovered.
- The arrows always have a border and control background, and dim to `.35` opacity when
  there is nowhere further to go.
- Buttons are `2.625rem` square, or `2rem` wide and `2.5rem` tall on phones, where the
  whole row fits a 360px screen.
- A list with only one page shows no selector.

Name its classes `lv-pager-*`. `lv-page` is the page container.

## Galleries

Visia, Vibia, and Lexia share one gallery design, built around the picture.

### Cards

- **Picture:** `3:2`, leading the card; Lexia shows the top of the first page, where the
  letterhead is. A hover lifts it by `2px`, scales it to `1.015`,
  and adds a shadow.
- **Name:** the readable name from `readableName`, with no upload prefix, extension, or
  underscores, in primary text at body size. Downloads keep the stored file name.
- **Tags:** the first three in the neutral `lv-tag-quiet` style, then a `+n` count. The
  `ai info` tag is hidden by `visibleTags`, because nearly every item has it. Phones show
  one tag and a count; list view shows up to eight.
- **Foot:** muted facts on the left and the `⋮` actions menu on the right. Visia shows
  dimensions and size; Vibia shows length, tempo when known, and size; Lexia shows type,
  financial year, pages, and size, with a "needs review" flag above them when one applies.
- **Favorite:** a heart on the picture, shown on hover, always shown when set or on touch
  screens. The Favorites section uses the same heart icon.
- **Grid:** three columns, four from `1760px` with a `1720px` page, and two on phones.
- **Search:** the box shows how many items match, such as "13 tracks".
- **Sort:** a `lv-select lv-sort` menu beside the search box, labelled "Sort by". It
  opens on **Similarity**, the order the library gives (best match first while searching),
  and offers date, name, and size orders, plus each app's own: Visia most pixels, Vibia
  artist, length, and tempo, Lexia document date, type, and pages. Each order has both
  directions as separate entries ("Name, A to Z" and "Name, Z to A"). Items with no value
  for the order go last in either direction, ties keep their similarity order, and the
  choice is remembered in local storage and goes back to the first page. The orders live
  in `sorting.ts` (`SORTS`, `sortItems`); sorting happens in the browser, on the filtered
  list, before paging.

### Full-window view

Opening an item fills the window: the content on a stage, with a `22.5rem` side panel
holding its position, name, description, tags, details, and actions. On phones the panel
sits below the stage.

- Arrow buttons and the left and right keys step through the filtered list, wrapping at
  the ends, and the gallery follows, so closing lands on the item last seen.
- Arrow keys do nothing while typing, in a focused list, or while an audio player has
  focus, because they act on those instead.
- Tags in the panel use the colored `lv-tag-*` styles. `tagSlot` picks the color from the
  tag's name, so a tag looks the same everywhere.
- Hidden items have no rename, favorite, or cover controls.
- Lexia's stage holds the page viewer: page number, zoom, a toggle that gives the page the
  full width with the details below, and pages to drag when zoomed. At 100% the whole page
  shows. Its panel is `32rem` wide, to fit the editable detail tables.
- Vibia's stage holds the 3:2 cover above the audio player, and each track gets a new
  player, so stepping never keeps playing the last track.

Browsers keep thumbnails for a year. A change of picture shape needs a new `shape` value
in the thumbnail URL as well as new sizes in `config.py`.

## Shared components

- **Buttons:** `lv-button`, plus `lv-button-primary` for the main action and
  `lv-button-active` for a pressed view toggle. `lv-icon-button` is the compact form
  inside cards and dialogs.
- **Dialogs:** `Dialog` wraps the browser's native `<dialog>`, which traps focus and
  closes on Escape. Escape inside an inline edit cancels only that edit.
- **Confirmation:** `ConfirmDialog` stays open with the reason if the action fails.
  Anything that permanently deletes a stored file asks first.
- **Toasts:** `useToast` from `Toasts`. They sit in a `popover="manual"` element, so
  they show above an open dialog.
- **Messages:** `lv-alert` for information, `lv-alert-error` for a failure, placed next
  to what failed. Show the backend's reason, not a generic error.
- **Forms:** `lv-form`, `lv-form-row`, and `lv-field`, with visible labels. A refused
  form keeps what was typed.

## Adding a missing picture

Where another app's picture is missing (a food photo in Vita, a track cover in Vibia, a
room, group, or light picture in Lumia), the place it would be offers to add one right
there, rather than sending people to Luna Visia.

- **Where:** on the empty or placeholder picture itself: Vita's **+** on a food's photo
  tile (`vt-photo-add`), Vibia's **Add cover** on the card's art (`lv-add-cover`) and
  **Upload cover** in its dialog, Lumia's **Add image** on a room or group's photo band
  and under a light's **Adjust**. It shows only while the item has no picture of its own;
  a placeholder or a borrowed catalog picture counts as none.
- **Look:** the `ImagePlus` icon with a short label, or the icon alone in a small tile,
  with an `aria-label` and a `title` that say the picture is stored in Luna Visia.
- **Flow:** the button opens the file picker straight away, with no dialog. The browser
  fits the picture to the app's shape (3:2 for covers and food) before sending it. While
  Visia captions it, the button is disabled with `aria-busy` and an `lv-busy-dot`, and a
  toast says it is being stored; a second toast says where it now shows. A failure keeps
  the old picture and shows the backend's reason.
- **Result:** the backend stores the picture in Visia with the app's tags, as the
  assistant's tools do, and every place that item appears shows it.

## Footer

Every Luna UI ends its signed-in page with `lv-footer`, the last thing inside `lv-page`:
the app's name and its release, as in `Luna Vita · v1.4.0`, in small muted text and
centred, or the name alone when the image has no release. The release comes from the git
tag: GitHub Actions passes it to the image as `LUNA_VERSION`, and it reaches the page through
`settings.version`. Each
gateway also names it in its health answer, which is how Luna Unia's cards show it.

## Behavior

- **Instant feedback.** Favorites, light controls, and similar changes show at once and
  roll back with a toast if the server refuses.
- **Finished choices.** Sliders and color pickers send one change when released, not
  one per pixel.
- **No lost typing.** Fresh server data must not overwrite a table cell someone is
  editing.
- **Motion.** Keep transitions under `200ms`, and respect `prefers-reduced-motion`.
- **Images.** Grids use small cached thumbnails from the gateway, not original files,
  with `loading="lazy"` and explicit `width` and `height`.

## Charts

Draw charts as plain SVG with no chart library. Use one y-axis per chart, thin marks,
and recessive grid lines. A chart with two or more series has a legend, and every chart
has a text alternative through `aria-label`, with values on hover. Values and labels use
text colors; only the mark carries the series color.

Fiora's `LineChart` uses `#3987e5` for income and `#d95926` for expenditure. Vita's
macro colors are protein `#4ade80`, carbs `#60a5fa`, fat `#fbbf24`, natural sugar
`#c084fc`, and added sugar `#fb7185`. A color always means the same thing within an app.

## App screens

Screens only one app has. Their classes use the app's prefix.

### Vita: Health Reports

- A report picker (date select with previous and next arrows) above one `vt-card` per
  report section: the report's facts in muted text, then counts of results in range,
  low, high, and abnormal.
- Results sit in folding panels, with **Find a test**, **Collapse all**, and an
  **Out of range** or **All results** toggle; a report with results out of range opens on
  **Out of range**.
- Status is a pill (`vt-lab-status`) outlined and written in its state color: in range
  `--vt-good`, low or high `--vt-warn`, abnormal `--vt-bad`, unknown muted. Never fill
  the pill, and always write the status in words, not color alone.
- Trends follow [Charts](#charts): one line in `vt-lab-line`, the report's reference
  interval as a shaded band (`vt-lab-band`), and dates under the points.
- **View PDF** opens the report in a dialog through Vita's own gateway, so it works
  on phones. Status describes a result against its printed range; the page never
  offers a diagnosis.

### Lumia: Home

- [Everyday screens](#everyday-screens) as built: `ll-now` cells for the whole house
  (`ll-power`, amber when any light is on), what is playing, the next schedule
  (`upcomingSchedule`), and Weather and Music pills (`ll-auto`, sky and magenta when on)
  that switch the automations through `PUT /api/automations/{weather,music}`.
- `ll-tile` per room, then **Other lights** (`ll-tiles-small`) for lights in no room.
  `tileGlow` layers a radial gradient per lit light, alpha `.2 + .5 × brightness`;
  a light showing black glows faint white so it still reads as on.
- `ll-dock` is the control panel: On / Off, brightness, **White** (temperature and
  presets) or **Color** (picker and chips), the everyday schemes (no weather, music, or
  per-key ones; playing first, then yours), and the selected lights, each of which can be
  selected alone.

### Lumia: Schemes, Schedules, Devices

- **List with a side panel** (`ll-split`): a `flex: 999 1 34rem` main column and a
  `20rem`–`26rem` side column that wraps below it under `960px`. A panel that edits
  (`ll-split-panel`) is sticky on desktop.
- **Schemes:** a **Plays on** chip row (`ll-plays-on`) shares Home's selection, so a
  scheme plays where Home has rooms selected; on phones the row scrolls sideways. Cards
  (`ll-scheme-card`) lead with the scheme's color strip, then **Play** or **Stop**, and
  edit and delete for your own; a playing card is outlined amber. The side column holds
  **Follow the weather** and **Follow the music**, each a switch over the schemes its
  slots play. Transitions follow below.
- **Schedules:** the Today strip, then rows (`ll-schedule`) with the time, what it does
  over its scheme's strip, the weekdays as letters, and a pause switch. Selecting a row
  opens it in the editor beside the list, where **Do** is one menu (turn on, turn off, or
  play a scheme), **On** is chips for every room and group plus a menu for one light, and
  days are round letter buttons.
- **Devices:** a setup strip (adding lights, finding moved ones, PC devices, location),
  rooms and groups as picture cards (`ll-place`), and every light in one table with its
  room as a menu, its kind and address, its status, and its picture and delete actions.

### Lumia: PC tab

- One `ll-panel ll-pc-card` per PC light: keyboards first, then mice and case parts.
  The head names the device, its zone or LED range, and how many keys or LEDs it has.
- The surface is an SVG drawn from the device's own layout: keys as rounded squares with
  their labels (`ll-pc-keyboard`), LEDs as dots in a row (`ll-pc-strip`). Press and drag
  paints like a brush; each key has a `<title>` with its name.
- Tools below it: brush color with the color chips and **Erase**, group buttons
  (WASD, Arrows, ...), background color, **Fill with brush**, **Rainbow**, **Clear**,
  brightness, a **Live** toggle, and **Save as scheme**. A pressed tool uses `ll-pc-on`
  and `aria-pressed`.
- With **Live** on, the device gets the pattern 250 ms after the last stroke, one
  request rather than one per key, following "Finished choices" under
  [Behavior](#behavior).

## Known deviations

These are differences in the code today. Each should either be fixed to match this
guide or deliberately accepted here.

| App | Deviation | Guide says |
| --- | --- | --- |
| Lumia, Fiora, Unia | Use the system monospace stack. | Self-hosted Source Code Pro. |
| Lumia, Fiora | Navbar gap is `.35rem` with a `1rem` bottom margin; selected and hovered labels turn to primary text, without the hover lift. | `.2rem`, `.2rem 0 1.15rem`, muted labels, and `translateY(-1px)`. |
| Visia, Vibia, Lexia | Navbar uses `grid-template-columns: repeat(3, 1fr)`. | `grid-auto-flow: column`, which fits any number of sections. Harmless with three. |
| Visia, Vibia, Lexia | In windows 1760px and wider, the page grows to `1720px` and the gallery to four columns, so the header and navbar no longer line up with the other apps at those widths. | `max-width: 1180px`. Accepted, because a gallery wastes most of a wide window at 1180px. |
| Vita | In windows 1760px and wider, the page grows to `1720px`, as in Visia, Vibia, and Lexia, with the nutrient tiles in one row and a wider side column. | `max-width: 1180px`. Accepted, because the food log and charts have room to use at that width. |
| Visia | The content security policy has no `font-src 'self' data:`, so the browser refuses the font's inlined files. | Add it, as Vibia and Lexia have. |
| Lumia | Deleting a light, room, scheme, or schedule has no confirmation. | Nothing here deletes a stored file, so this is accepted for now. Revisit if mistaken deletes happen. |
| Unia | In windows 1760px and wider, the page grows to `1720px` and the card grid to four columns, as in Visia, Vibia, and Lexia. | `max-width: 1180px`. Accepted, because the cards have room to use at that width. |
| Unia | No navbar. Its one page is a grid of app cards, and the status summary above the grid (`.lu-summary`) takes the navbar's surface and `.2rem 0 1.15rem` margin. | Full-width navbar below the header. Accepted: Unia has nothing to navigate between. |
| Socia | No navbar, so no tab bar on phones. Its one screen is the chat, already laid out for phones. | [Phone layout](#phone-layout). Accepted: there are no sections to switch between. |
| Unia | Checks each app at `/_stcore/health`, which every gateway keeps as an alias of `/health`. | Move to `/health` and remove the alias once every app has merged its React UI. |

## Checklists

### After any header or shell change

1. The logo and title start at the established desktop height.
2. Logo, title, subtitle, and controls stay vertically centred.
3. Nothing renders above the header.
4. On phones, the brand stays on one line, the subtitle is hidden, the control row stays
   on one line, Sign out shows only its icon, the page does not scroll sideways, and the
   tabs sit along the bottom with nothing hidden under them.
5. A hard refresh gives the same position as moving between sections.
6. `npm run build` passes, and the browser console shows no errors.
7. Check the running container, not just the source. Compose Watch restarts the gateway
   on Python changes and rebuilds the image on changes under `ui/web/src`; without it,
   rebuild that app's UI container before reviewing.

### Before approving a shell change across apps

1. Open every app at the same viewport width and 100% zoom.
2. Measure the navbar's bounding-box `top`, `left`, and `width`; they agree to within
   one device pixel, apart from the accepted Visia and Unia deviations.
3. Check default, selected, hover, keyboard-focus, hard-refresh, and phone states. Do not
   approve from a source review alone.
4. Update this guide first, then apply the change to every app together.

### Adding a new Luna app

1. Copy `styles.css`, `main.tsx`, `http.ts`, and the shared components from the most
   recently migrated app, then the gateway, `sso.py`, and the `SSOConfig` block, which
   stay identical across apps.
2. Use the `.lv-header` markup, logo box, title, and "Private AI-managed …" subtitle.
3. Put the app's main action on the right as a primary button, followed by Sign out.
4. Use a full-width `lv-nav`; change only labels and icons.
5. Forward only the backend routes the UI uses, and put every setting in `config.py`.
   A gallery follows [Galleries](#galleries), and any paged list uses `Pager`.
6. Give the app's own rules a short class prefix, add the app to the tables in this
   guide, and copy the guide into every repo.
