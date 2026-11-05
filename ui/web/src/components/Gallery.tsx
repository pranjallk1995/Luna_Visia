import { CheckSquare, EyeOff, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  thumbnailUrl,
  useImageActions,
  useImages,
  type ImageRecord,
  type NamedMetadataChange,
  type Section,
  type Settings,
} from "../api";
import {
  clickedSelection,
  displayName,
  inSection,
  matching,
  messageOf,
  outcomeMessage,
  pageCount,
  pageOf,
  selectionLabel,
  type BatchOutcome,
} from "../lib";
import { BulkBar, type BulkAction } from "./BulkBar";
import { BulkEditDialog } from "./BulkEditDialog";
import { ConfirmDialog } from "./ConfirmDialog";
import { ImageCard, type CardAction } from "./ImageCard";
import { Pager } from "./Pager";
import { PreviewDialog } from "./PreviewDialog";
import { SearchBox } from "./SearchBox";
import { useToast } from "./Toasts";

const COPY: Record<Section, { label: string; noMatch: string }> = {
  view: { label: "Search by image name", noMatch: "No filenames or tags match your search." },
  favorites: { label: "Search Favorites by image name", noMatch: "No favorite filenames or tags match your search." },
  hidden: { label: "Search hidden vault by image name", noMatch: "No hidden filenames or tags match your search." },
};

type Pending = { kind: "delete" | "hide"; image: ImageRecord } | null;

// Kept from when the prompt opened: the selection empties on screen once the change starts.
type BulkPending = { kind: "delete" | "hide"; names: string[]; subject: string } | { kind: "edit" } | null;

type Props = {
  section: Section;
  layout: "grid" | "list";
  settings: Settings;
};

export function Gallery({ section, layout, settings }: Props) {
  const { data, isPending, error } = useImages(section);
  const actions = useImageActions(section, settings.batch_size);
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [openName, setOpenName] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [selecting, setSelecting] = useState(false);
  const [selection, setSelection] = useState<ReadonlySet<string>>(() => new Set());
  const [bulkPending, setBulkPending] = useState<BulkPending>(null);

  const inThisSection = useMemo(() => inSection(data ?? [], section), [data, section]);
  const filtered = useMemo(() => matching(inThisSection, query), [inThisSection, query]);
  // Read from the section's images, so anything deleted or moved away drops out by itself.
  const selected = useMemo(() => inThisSection.filter((image) => selection.has(image.name)), [inThisSection, selection]);
  const selectedNames = useMemo(() => selected.map((image) => image.name), [selected]);
  const outside = selected.length - filtered.filter((image) => selection.has(image.name)).length;
  const count = pageCount(filtered.length, settings.images_per_page);
  const current = Math.min(page, count - 1);
  const images = pageOf(filtered, current, settings.images_per_page);
  const openIndex = filtered.findIndex((image) => image.name === openName);

  // Fetch the next page's thumbnails while idle, so paging forward shows them at once.
  useEffect(() => {
    const next = pageOf(filtered, current + 1, settings.images_per_page);
    const handle = setTimeout(() => next.forEach((image) => (new Image().src = thumbnailUrl(image))), 300);
    return () => clearTimeout(handle);
  }, [filtered, current, settings.images_per_page]);

  const { setFavorite, restore } = actions;
  const onAction = useCallback(
    (action: CardAction, image: ImageRecord) => {
      if (action === "open") setOpenName(image.name);
      if (action === "delete" || action === "hide") setPending({ kind: action, image });
      if (action === "favorite") {
        setFavorite.mutate(
          { name: image.name, favorite: !image.favorite },
          { onError: (failure) => toast(messageOf(failure, "Could not update Favorite status.")) },
        );
      }
      if (action === "restore") {
        restore.mutate(image.name, { onError: (failure) => toast(messageOf(failure, "Could not restore the image.")) });
      }
    },
    // Mutation objects change identity as they run; their mutate functions do not.
    [setFavorite.mutate, restore.mutate, toast],
  );

  // Refs keep onSelect stable, so selecting one card does not re-render the others.
  const order = useRef<string[]>([]);
  order.current = filtered.map((image) => image.name);
  const anchor = useRef<string | null>(null);

  const onSelect = useCallback((image: ImageRecord, extend: boolean) => {
    const from = extend ? anchor.current : null;
    anchor.current = image.name;
    setSelection((current) => clickedSelection(current, order.current, image.name, from));
  }, []);

  const toggleSelecting = () => {
    setSelecting((current) => !current);
    setSelection(new Set());
    anchor.current = null;
  };

  /** Say how a bulk action went, leaving only the images it could not change selected. */
  const report = (outcome: BatchOutcome, action: string) => {
    toast(outcomeMessage(outcome, action));
    if (outcome.failed.length) setSelection(new Set(outcome.failed.map((failure) => failure.name)));
  };

  const setFavorites = (favorite: boolean) => {
    const names = selected.filter((image) => Boolean(image.favorite) !== favorite).map((image) => image.name);
    actions.setFavorites
      .mutateAsync({ names, favorite })
      .then((outcome) => report(outcome, favorite ? "added to Favorites" : "removed from Favorites"))
      .catch((failure) => toast(messageOf(failure, "Could not update Favorite status.")));
  };

  const onBulkAction = (action: BulkAction) => {
    if (action === "favorite" || action === "unfavorite") return setFavorites(action === "favorite");
    if (action === "restore") {
      return void actions.restoreMany
        .mutateAsync(selectedNames)
        .then((outcome) => report(outcome, "restored"))
        .catch((failure) => toast(messageOf(failure, "Could not restore the images.")));
    }
    if (action === "edit") return setBulkPending({ kind: "edit" });
    // A destructive prompt names what the search is not showing, so nothing goes unnoticed.
    const subject = `${selectionLabel(selectedNames)}${outside > 0 ? ` (${outside} outside this search)` : ""}`;
    setBulkPending({ kind: action, names: selectedNames, subject });
  };

  const editSelected = async (changes: NamedMetadataChange[]) =>
    report(await actions.updateMetadataMany.mutateAsync(changes), "updated");

  const changeQuery = (value: string) => {
    setQuery(value);
    setPage(0);
  };

  const changePage = (target: number) => {
    setPage(target);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  // The gallery follows along behind the viewer, so closing it lands on the image last seen.
  const stepViewer = (delta: number) => {
    const index = (openIndex + delta + filtered.length) % filtered.length;
    setOpenName(filtered[index].name);
    setPage(Math.floor(index / settings.images_per_page));
  };

  return (
    <>
      <div className="lv-gallery-tools">
        <SearchBox label={COPY[section].label} value={query} count={data ? filtered.length : undefined} onChange={changeQuery} />
        <button
          className={`lv-button lv-select-toggle${selecting ? " lv-button-active" : ""}`}
          type="button"
          aria-pressed={selecting}
          disabled={!selecting && !filtered.length}
          onClick={toggleSelecting}
        >
          {selecting ? <X size={17} /> : <CheckSquare size={17} />}
          <span>{selecting ? "Done" : "Select"}</span>
        </button>
      </div>
      {error && <p className="lv-alert lv-alert-error">{error.message}</p>}
      {isPending && <div className="lv-skeleton-grid" aria-label="Loading images" />}
      {data && !filtered.length && (
        <p className="lv-alert">{query.trim() ? COPY[section].noMatch : "No images are currently stored in Luna Visia."}</p>
      )}
      {images.length > 0 && (
        <>
          <div className={`lv-gallery lv-gallery-${layout}`} key={`${section}-${current}`}>
            {images.map((image) => (
              <ImageCard
                key={image.name}
                image={image}
                section={section}
                layout={layout}
                onAction={onAction}
                selecting={selecting}
                selected={selection.has(image.name)}
                onSelect={onSelect}
              />
            ))}
          </div>
          <Pager page={current} count={count} onChange={changePage} />
        </>
      )}
      {selecting && (
        <BulkBar
          section={section}
          selected={selected}
          outside={outside}
          matching={filtered.length}
          onSelectAll={() => setSelection((current) => new Set([...current, ...order.current]))}
          onClear={() => setSelection(new Set())}
          onAction={onBulkAction}
        />
      )}
      {openIndex >= 0 && (
        <PreviewDialog
          image={filtered[openIndex]}
          position={openIndex + 1}
          total={filtered.length}
          next={filtered[(openIndex + 1) % filtered.length]}
          section={section}
          settings={settings}
          actions={actions}
          onStep={stepViewer}
          onAction={onAction}
          onClose={() => setOpenName(null)}
        />
      )}
      {pending?.kind === "delete" && (
        <ConfirmDialog
          title="Delete image?"
          message="Permanently delete"
          subject={displayName(pending.image.name)}
          action="Delete"
          icon={Trash2}
          run={() => actions.remove.mutateAsync(pending.image.name)}
          onClose={() => setPending(null)}
        />
      )}
      {pending?.kind === "hide" && (
        <ConfirmDialog
          title="Hide image?"
          message="Move to Hidden Images:"
          subject={displayName(pending.image.name)}
          action="Hide"
          icon={EyeOff}
          run={() => actions.hide.mutateAsync(pending.image.name)}
          onClose={() => setPending(null)}
        />
      )}
      {bulkPending?.kind === "delete" && (
        <ConfirmDialog
          title="Delete images?"
          message="Permanently delete"
          subject={bulkPending.subject}
          action="Delete"
          icon={Trash2}
          run={async () => report(await actions.removeMany.mutateAsync(bulkPending.names), "deleted")}
          onClose={() => setBulkPending(null)}
        />
      )}
      {bulkPending?.kind === "hide" && (
        <ConfirmDialog
          title="Hide images?"
          message="Move to Hidden Images:"
          subject={bulkPending.subject}
          action="Hide"
          icon={EyeOff}
          run={async () => report(await actions.hideMany.mutateAsync(bulkPending.names), "hidden")}
          onClose={() => setBulkPending(null)}
        />
      )}
      {bulkPending?.kind === "edit" && (
        <BulkEditDialog images={selected} settings={settings} run={editSelected} onClose={() => setBulkPending(null)} />
      )}
    </>
  );
}
