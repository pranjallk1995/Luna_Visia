import { ArrowLeft, ArrowRight, Check, Download, Eye, EyeOff, Heart, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";

import { downloadUrl, fileUrl, useGenerator, type ImageActions, type ImageRecord, type Section, type Settings } from "../api";
import { displayName, messageOf, normalizedTag, readableName, sizeLabel, splitName, tagSlot, visibleTags } from "../lib";
import { useModal } from "./Dialog";
import type { CardAction } from "./ImageCard";
import { useToast } from "./Toasts";

type EditableProps = {
  label: string;
  value: string;
  /** What to show while not editing, when it differs from the text being edited. */
  shown?: string;
  maxLength: number;
  placeholder?: string;
  /** Resolve to close the editor; reject to keep it open with the error. */
  save: (draft: string) => Promise<unknown>;
};

/** Text with a pencil: Enter or the checkmark saves, Escape cancels. */
function EditableText({ label, value, shown, maxLength, placeholder, save }: EditableProps) {
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await save(draft ?? "");
      setDraft(null);
    } catch (failure) {
      setError(messageOf(failure, `Could not update ${label.toLowerCase()}.`));
    } finally {
      setBusy(false);
    }
  };

  if (draft === null) {
    return (
      <div className="lv-detail-line lv-detail-line-top">
        <span className="lv-detail-text">{shown ?? value}</span>
        <button className="lv-icon-button" type="button" aria-label={`Edit ${label.toLowerCase()}`} onClick={() => setDraft(value)}>
          <Pencil size={15} />
        </button>
      </div>
    );
  }
  return (
    <form onSubmit={submit}>
      <div className="lv-detail-line">
        <input
          className="lv-input"
          aria-label={label}
          autoFocus
          value={draft}
          maxLength={maxLength}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              // Cancel only this edit, not the whole dialog.
              event.preventDefault();
              event.stopPropagation();
              setDraft(null);
              setError("");
            }
          }}
        />
        <button className="lv-icon-button" type="submit" aria-label={`Save ${label.toLowerCase()}`} disabled={busy}>
          <Check size={16} />
        </button>
      </div>
      {error && <p className="lv-alert lv-alert-error">{error}</p>}
    </form>
  );
}

type TagsProps = {
  image: ImageRecord;
  maxTags: number;
  actions: ImageActions;
};

function TagEditor({ image, maxTags, actions }: TagsProps) {
  const toast = useToast();
  const [draft, setDraft] = useState<string | null>(null);
  const tags = image.tags ?? [];

  const removeTag = (tag: string) =>
    actions.updateMetadata.mutate(
      { name: image.name, change: { remove_tags: [tag] } },
      { onError: (failure) => toast(messageOf(failure, "Could not remove the tag.")) },
    );

  const addTag = (event: FormEvent) => {
    event.preventDefault();
    const tag = (draft ?? "").trim();
    if (!tag) return toast("Enter a tag name before adding it.");
    if (tags.some((existing) => normalizedTag(existing) === normalizedTag(tag))) {
      return toast("That tag is already on this image.");
    }
    if (tags.length >= maxTags) {
      return toast(`Maximum of ${maxTags} tags reached. Remove a tag before adding another.`);
    }
    actions.updateMetadata.mutate(
      { name: image.name, change: { add_tags: [tag] } },
      {
        onSuccess: () => setDraft(null),
        onError: () => toast("The tag could not be saved. Please try again."),
      },
    );
  };

  return (
    <div className="lv-preview-tags">
      {visibleTags(tags).map((tag) => (
        <button
          className={`lv-tag lv-tag-${tagSlot(tag)} lv-tag-removable`}
          type="button"
          key={tag}
          aria-label={`Remove tag ${tag}`}
          onClick={() => removeTag(tag)}
        >
          {tag}
          <X size={12} />
        </button>
      ))}
      {draft === null ? (
        <button className="lv-icon-button" type="button" aria-label="Add a tag" onClick={() => setDraft("")}>
          <Plus size={16} />
        </button>
      ) : (
        <form className="lv-tag-form" onSubmit={addTag}>
          <input
            className="lv-input"
            aria-label="New tag"
            autoFocus
            placeholder="Tag name"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                setDraft(null);
              }
            }}
          />
          <button className="lv-icon-button" type="submit" aria-label="Save tag">
            <Check size={16} />
          </button>
        </form>
      )}
    </div>
  );
}

type Props = {
  image: ImageRecord;
  /** Where this image sits in the gallery being browsed, from 1. */
  position: number;
  total: number;
  /** The image the next-arrow leads to, fetched ahead so stepping forward is immediate. */
  next: ImageRecord;
  section: Section;
  settings: Settings;
  actions: ImageActions;
  onStep: (delta: number) => void;
  onAction: (action: CardAction, image: ImageRecord) => void;
  onClose: () => void;
};

/** One image filling the window, with its details and actions in a side panel. */
export function PreviewDialog({ image, position, total, next, section, settings, actions, onStep, onAction, onClose }: Props) {
  const ref = useModal();
  const toast = useToast();
  const shownName = readableName(image.name);
  const { stem: storedStem } = splitName(displayName(image.name));
  const { suffix } = splitName(image.name);
  const { data: generator } = useGenerator(image, section !== "hidden");

  useEffect(() => {
    new Image().src = fileUrl(next);
  }, [next]);

  const rename = async (draft: string) => {
    const stem = draft.trim();
    if (stem === storedStem) return;
    const newName = `${stem}${suffix}`;
    await actions.rename.mutateAsync({ name: image.name, newName });
    toast(`Renamed to ${newName}.`);
    // The file moved, so this dialog's image no longer exists under its old name.
    onClose();
  };

  const stepWithArrowKeys = (event: KeyboardEvent) => {
    if (event.target instanceof HTMLInputElement) return;
    if (event.key === "ArrowLeft") onStep(-1);
    if (event.key === "ArrowRight") onStep(1);
  };

  return (
    <dialog
      ref={ref}
      className="lv-viewer"
      aria-label={shownName}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={stepWithArrowKeys}
    >
      <div className="lv-viewer-body">
        <div
          className="lv-stage"
          onClick={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <img src={fileUrl(image)} alt={image.caption ?? shownName} />
          {total > 1 && (
            <>
              <button className="lv-step lv-step-prev" type="button" aria-label="Previous image" onClick={() => onStep(-1)}>
                <ArrowLeft size={18} />
              </button>
              <button className="lv-step lv-step-next" type="button" aria-label="Next image" onClick={() => onStep(1)}>
                <ArrowRight size={18} />
              </button>
            </>
          )}
        </div>
        {/* Keyed so a half-finished edit never carries over to the next image. */}
        <aside className="lv-panel" aria-label="Image details" key={image.name}>
          <div className="lv-panel-top">
            <span>
              {position} of {total}
            </span>
            <button className="lv-icon-button" type="button" aria-label="Close" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
          <div className="lv-viewer-name">
            {section === "hidden" ? (
              <span className="lv-detail-text">{shownName}</span>
            ) : (
              <EditableText
                key={storedStem}
                label="Name"
                value={storedStem}
                shown={shownName}
                maxLength={settings.name_max_chars}
                placeholder={`Letters, numbers, and underscores (${suffix} is kept)`}
                save={rename}
              />
            )}
          </div>
          <section>
            <h3 className="lv-section">Description</h3>
            <EditableText
              label="Description"
              value={image.caption ?? ""}
              maxLength={settings.caption_max_chars}
              save={(caption) => actions.updateMetadata.mutateAsync({ name: image.name, change: { caption } })}
            />
          </section>
          <section>
            <h3 className="lv-section">Tags</h3>
            <TagEditor image={image} maxTags={settings.max_tags} actions={actions} />
          </section>
          <section>
            <h3 className="lv-section">Details</h3>
            <dl className="lv-facts">
              <dt>Size</dt>
              <dd>{sizeLabel(image.size_bytes)}</dd>
              <dt>Pixels</dt>
              <dd>
                {image.width} × {image.height}
              </dd>
              {suffix && (
                <>
                  <dt>Format</dt>
                  <dd>{suffix.slice(1).toUpperCase()}</dd>
                </>
              )}
              {generator && (
                <>
                  <dt>Made with</dt>
                  <dd>{generator}</dd>
                </>
              )}
              {image.analysis_model && (
                <>
                  <dt>Analyzed by</dt>
                  <dd>{image.analysis_model}</dd>
                </>
              )}
            </dl>
          </section>
          <div className="lv-panel-actions">
            {section !== "hidden" && (
              <button
                className={`lv-icon-button${image.favorite ? " lv-favorite-active" : ""}`}
                type="button"
                aria-label={image.favorite ? "Remove from favorites" : "Add to favorites"}
                aria-pressed={Boolean(image.favorite)}
                title={image.favorite ? "Remove from favorites" : "Add to favorites"}
                onClick={() => onAction("favorite", image)}
              >
                <Heart size={18} fill={image.favorite ? "currentColor" : "none"} />
              </button>
            )}
            <a className="lv-icon-button" href={downloadUrl(image, displayName(image.name))} aria-label="Download" title="Download">
              <Download size={18} />
            </a>
            {section === "view" && (
              <button className="lv-icon-button" type="button" aria-label="Hide" title="Hide" onClick={() => onAction("hide", image)}>
                <EyeOff size={18} />
              </button>
            )}
            {section === "hidden" && (
              <button className="lv-icon-button" type="button" aria-label="Restore" title="Restore" onClick={() => onAction("restore", image)}>
                <Eye size={18} />
              </button>
            )}
            <button className="lv-icon-button" type="button" aria-label="Delete" title="Delete" onClick={() => onAction("delete", image)}>
              <Trash2 size={18} />
            </button>
          </div>
        </aside>
      </div>
    </dialog>
  );
}
