import { Download, Eye, EyeOff, Heart, MoreVertical, Trash2 } from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";

import { downloadUrl, thumbnailUrl, type ImageRecord, type Section } from "../api";
import { displayName, sizeLabel } from "../lib";

export type CardAction = "open" | "favorite" | "delete" | "hide" | "restore";

type Props = {
  image: ImageRecord;
  section: Section;
  layout: "grid" | "list";
  onAction: (action: CardAction, image: ImageRecord) => void;
};

function CardMenu({ image, section, onAction }: Pick<Props, "image" | "section" | "onAction">) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const name = displayName(image.name);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const act = (action: CardAction) => {
    setOpen(false);
    onAction(action, image);
  };

  return (
    <div className="lv-menu" ref={ref}>
      <button
        className="lv-menu-trigger"
        type="button"
        aria-label={`Actions for ${name}`}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <MoreVertical size={18} />
      </button>
      {open && (
        <div className="lv-menu-items">
          <a className="lv-icon-button" href={downloadUrl(image, name)} aria-label="Download" title="Download">
            <Download size={17} />
          </a>
          <button className="lv-icon-button" type="button" aria-label="Delete" title="Delete" onClick={() => act("delete")}>
            <Trash2 size={17} />
          </button>
          {section === "view" && (
            <button className="lv-icon-button" type="button" aria-label="Hide" title="Hide" onClick={() => act("hide")}>
              <EyeOff size={17} />
            </button>
          )}
          {section === "hidden" && (
            <button className="lv-icon-button" type="button" aria-label="Restore" title="Restore" onClick={() => act("restore")}>
              <Eye size={17} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ImageCardBase({ image, section, layout, onAction }: Props) {
  const name = displayName(image.name);
  return (
    <article className={`lv-card lv-card-${layout}`}>
      <div className="lv-thumbnail">
        <button className="lv-thumbnail-open" type="button" aria-label={`Open ${name}`} onClick={() => onAction("open", image)}>
          <img src={thumbnailUrl(image)} alt="" width={420} height={248} loading="lazy" decoding="async" />
        </button>
        {section !== "hidden" && (
          <button
            className={`lv-favorite${image.favorite ? " lv-favorite-active" : ""}`}
            type="button"
            aria-label={image.favorite ? `Remove ${name} from favorites` : `Add ${name} to favorites`}
            aria-pressed={Boolean(image.favorite)}
            onClick={() => onAction("favorite", image)}
          >
            <Heart size={16} fill={image.favorite ? "currentColor" : "none"} />
          </button>
        )}
      </div>
      <div className="lv-card-details">
        <div className="lv-card-title">
          <span className="lv-card-name" title={name}>
            {name}
          </span>
          <CardMenu image={image} section={section} onAction={onAction} />
        </div>
        <div className="lv-card-tags">
          {(image.tags ?? []).map((tag, index) => (
            <span className={`lv-tag lv-tag-${index % 6}`} key={tag}>
              {tag}
            </span>
          ))}
        </div>
        <div className="lv-card-facts">
          <span>{sizeLabel(image.size_bytes)}</span>
          <span>·</span>
          <span>
            {image.width} × {image.height}
          </span>
        </div>
      </div>
    </article>
  );
}

// Memoized so typing in search or toggling one favorite re-renders only what changed.
export const ImageCard = memo(ImageCardBase);
