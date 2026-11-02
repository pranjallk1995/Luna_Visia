import { EyeOff, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { thumbnailUrl, useImageActions, useImages, type ImageRecord, type Section, type Settings } from "../api";
import { displayName, inSection, matching, messageOf, pageCount, pageOf } from "../lib";
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

type Props = {
  section: Section;
  layout: "grid" | "list";
  settings: Settings;
};

export function Gallery({ section, layout, settings }: Props) {
  const { data, isPending, error } = useImages(section);
  const actions = useImageActions(section);
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [openName, setOpenName] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);

  const filtered = useMemo(() => matching(inSection(data ?? [], section), query), [data, section, query]);
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
      <SearchBox label={COPY[section].label} value={query} count={data ? filtered.length : undefined} onChange={changeQuery} />
      {error && <p className="lv-alert lv-alert-error">{error.message}</p>}
      {isPending && <div className="lv-skeleton-grid" aria-label="Loading images" />}
      {data && !filtered.length && (
        <p className="lv-alert">{query.trim() ? COPY[section].noMatch : "No images are currently stored in Luna Visia."}</p>
      )}
      {images.length > 0 && (
        <>
          <div className={`lv-gallery lv-gallery-${layout}`} key={`${section}-${current}`}>
            {images.map((image) => (
              <ImageCard key={image.name} image={image} section={section} layout={layout} onAction={onAction} />
            ))}
          </div>
          <Pager page={current} count={count} onChange={changePage} />
        </>
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
    </>
  );
}
