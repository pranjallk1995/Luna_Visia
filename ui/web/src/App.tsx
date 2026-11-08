import { Eye, Heart, LayoutGrid, List, Lock, LogOut, Upload, type LucideIcon } from "lucide-react";
import { useState } from "react";

import { useSession, type Section, type Session } from "./api";
import { Gallery } from "./components/Gallery";
import { HiddenGate } from "./components/HiddenGate";
import { UploadDialog } from "./components/UploadDialog";
import { remember, remembered } from "./lib";
import { useUploads } from "./uploads";

const SUBTITLE = "Private AI-managed image storage";

const SECTIONS: { id: Section; label: string; icon: LucideIcon }[] = [
  { id: "view", label: "View Images", icon: Eye },
  { id: "favorites", label: "Favorite Images", icon: Heart },
  { id: "hidden", label: "Hidden Images", icon: Lock },
];

const LAYOUTS = ["grid", "list"] as const;
type Layout = (typeof LAYOUTS)[number];

function Brand() {
  return (
    <div className="lv-header">
      <img className="lv-mark" src="/logo.png" alt="Luna Visia logo" />
      <div>
        <h1 className="lv-title">Luna Visia</h1>
        <p className="lv-subtitle">{SUBTITLE}</p>
      </div>
    </div>
  );
}

function SignInPage({ url }: { url: string }) {
  return (
    <main className="lv-page lv-gate">
      <Brand />
      <a className="lv-google" href={url}>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
          <path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.07H12v3.92h5.38a4.6 4.6 0 0 1-2 3.02v2.54h3.24c1.9-1.75 2.98-4.33 2.98-7.41z" />
          <path fill="#34A853" d="M12 22c2.7 0 4.97-.9 6.62-2.43l-3.24-2.54c-.9.6-2.05.96-3.38.96-2.6 0-4.81-1.76-5.6-4.13H3.06v2.62A10 10 0 0 0 12 22z" />
          <path fill="#FBBC05" d="M6.4 13.86A6 6 0 0 1 6.08 12c0-.65.11-1.28.32-1.86V7.52H3.06A10 10 0 0 0 2 12c0 1.61.39 3.14 1.06 4.48l3.34-2.62z" />
          <path fill="#EA4335" d="M12 6.01c1.47 0 2.79.5 3.83 1.5l2.87-2.87A9.63 9.63 0 0 0 12 2a10 10 0 0 0-8.94 5.52l3.34 2.62C7.19 7.77 9.4 6.01 12 6.01z" />
        </svg>
        Continue with Google
      </a>
      <p className="lv-caption">One sign-in opens every Luna app.</p>
    </main>
  );
}

function ForbiddenPage({ email, logoutUrl }: { email?: string; logoutUrl: string }) {
  return (
    <main className="lv-page lv-gate">
      <Brand />
      <p className="lv-alert lv-alert-error">{email ?? "This account"} is not authorized to access Luna Visia.</p>
      <a className="lv-button" href={logoutUrl}>
        <LogOut size={16} />
        Sign out
      </a>
    </main>
  );
}

function Workspace({ session }: { session: Extract<Session, { status: "signed_in" }> }) {
  const [section, setSection] = useState<Section>(() =>
    remembered("lv-section", ["view", "favorites", "hidden"] as const, "view"),
  );
  const [layout, setLayout] = useState<Layout>(() => remembered("lv-layout", LAYOUTS, "grid"));
  const [uploading, setUploading] = useState(false);
  const uploads = useUploads();

  const chooseSection = (id: Section) => {
    setSection(id);
    remember("lv-section", id);
  };

  const chooseLayout = (id: Layout) => {
    setLayout(id);
    remember("lv-layout", id);
  };

  return (
    <main className="lv-page">
      <div className="lv-header-row">
        <Brand />
        <div className="lv-header-actions">
          <button
            className={`lv-button${layout === "grid" ? " lv-button-active" : ""}`}
            type="button"
            aria-label="Grid view"
            aria-pressed={layout === "grid"}
            onClick={() => chooseLayout("grid")}
          >
            <LayoutGrid size={18} />
          </button>
          <button
            className={`lv-button${layout === "list" ? " lv-button-active" : ""}`}
            type="button"
            aria-label="List view"
            aria-pressed={layout === "list"}
            onClick={() => chooseLayout("list")}
          >
            <List size={18} />
          </button>
          <button className="lv-button lv-button-primary lv-header-main" type="button" onClick={() => setUploading(true)}>
            <Upload size={17} />
            Upload Images
            {uploads.progress && <span className="lv-busy-dot" aria-label="Uploading" />}
          </button>
          <a className="lv-button lv-sign-out" href={session.logout_url}>
            <LogOut size={16} />
            Sign out
          </a>
        </div>
      </div>

      <nav className="lv-nav" aria-label="Gallery section">
        {SECTIONS.map(({ id, label, icon: Icon }) => (
          <button
            className={section === id ? "lv-nav-item lv-nav-active" : "lv-nav-item"}
            type="button"
            key={id}
            aria-label={label}
            aria-current={section === id ? "page" : undefined}
            onClick={() => chooseSection(id)}
          >
            <Icon size={17} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      {section === "hidden" ? (
        <HiddenGate noun="images">
          <Gallery key={section} section={section} layout={layout} settings={session.settings} />
        </HiddenGate>
      ) : (
        <Gallery key={section} section={section} layout={layout} settings={session.settings} />
      )}

      {uploading && <UploadDialog uploads={uploads} settings={session.settings} onClose={() => setUploading(false)} />}
      <footer className="lv-footer">Luna Visia{session.settings.version && ` · v${session.settings.version}`}</footer>
    </main>
  );
}

export function App() {
  const { data: session, error, refetch } = useSession();

  if (error) {
    return (
      <main className="lv-page lv-gate">
        <Brand />
        <p className="lv-alert lv-alert-error">Luna Visia could not be reached.</p>
        <button className="lv-button" type="button" onClick={() => refetch()}>
          Try again
        </button>
      </main>
    );
  }
  if (!session) return null;
  if (session.status === "signed_out") return <SignInPage url={session.login_url} />;
  if (session.status === "forbidden") return <ForbiddenPage email={session.email} logoutUrl={session.logout_url} />;
  return <Workspace session={session} />;
}
