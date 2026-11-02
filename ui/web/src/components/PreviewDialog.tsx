import { Bot, Check, FileImage, Pencil, Plus, Tag, Text, X } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";

import { fileUrl, type ImageActions, type ImageRecord, type Section, type Settings } from "../api";
import { displayName, messageOf, normalizedTag, splitName } from "../lib";
import { Dialog } from "./Dialog";
import { useToast } from "./Toasts";

function DetailRow({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="lv-detail">
      <span className="lv-detail-icon">{icon}</span>
      <div className="lv-detail-content">{children}</div>
    </div>
  );
}

type EditableProps = {
  label: string;
  value: string;
  maxLength: number;
  placeholder?: string;
  /** Resolve to close the editor; reject to keep it open with the error. */
  save: (draft: string) => Promise<unknown>;
};

/** Text with a pencil: Enter or the checkmark saves, Escape cancels. */
function EditableText({ label, value, maxLength, placeholder, save }: EditableProps) {
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
      <div className="lv-detail-line">
        <span className="lv-detail-text">{value}</span>
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
    <div className="lv-detail-line lv-detail-line-top">
      <div className="lv-preview-tags">
        {tags.map((tag, index) => (
          <button
            className={`lv-tag lv-tag-${index % 6} lv-tag-removable`}
            type="button"
            key={tag}
            aria-label={`Remove tag ${tag}`}
            onClick={() => removeTag(tag)}
          >
            {tag}
            <X size={12} />
          </button>
        ))}
      </div>
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
  section: Section;
  settings: Settings;
  actions: ImageActions;
  onClose: () => void;
};

/** One image full size, with its name, caption, and tags editable in place. */
export function PreviewDialog({ image, section, settings, actions, onClose }: Props) {
  const toast = useToast();
  const shownName = displayName(image.name);
  const { stem: shownStem } = splitName(shownName);
  const { suffix } = splitName(image.name);

  const rename = async (draft: string) => {
    const stem = draft.trim();
    if (stem === shownStem) return;
    const newName = `${stem}${suffix}`;
    await actions.rename.mutateAsync({ name: image.name, newName });
    toast(`Renamed to ${newName}.`);
    // The file moved, so this dialog's image no longer exists under its old name.
    onClose();
  };

  return (
    <Dialog title={shownName} size="large" onClose={onClose}>
      <img className="lv-preview-image" src={fileUrl(image)} alt={image.caption ?? shownName} />
      {section !== "hidden" && (
        <DetailRow icon={<FileImage size={16} />}>
          <EditableText
            key={shownStem}
            label="Name"
            value={shownStem}
            maxLength={settings.name_max_chars}
            placeholder={`Letters, numbers, and underscores (${suffix} is kept)`}
            save={rename}
          />
        </DetailRow>
      )}
      <DetailRow icon={<Text size={16} />}>
        <EditableText
          label="Description"
          value={image.caption ?? ""}
          maxLength={settings.caption_max_chars}
          save={(caption) => actions.updateMetadata.mutateAsync({ name: image.name, change: { caption } })}
        />
      </DetailRow>
      <DetailRow icon={<Tag size={16} />}>
        <TagEditor image={image} maxTags={settings.max_tags} actions={actions} />
      </DetailRow>
      {image.analysis_model && (
        <DetailRow icon={<Bot size={16} />}>
          <span className="lv-detail-text">Analyzed by {image.analysis_model}</span>
        </DetailRow>
      )}
    </Dialog>
  );
}
