/** The gateway's routes, and React Query hooks that keep galleries in step with them. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { inBatches, type BatchFailure } from "./lib";

export type Section = "view" | "favorites" | "hidden";

export type ImageRecord = {
  name: string;
  caption?: string | null;
  tags?: string[] | null;
  favorite?: boolean;
  analysis_model?: string | null;
  size_bytes: number;
  width: number;
  height: number;
  version: number;
};

export type Settings = {
  images_per_page: number;
  batch_size: number;
  max_tags: number;
  name_max_chars: number;
  caption_max_chars: number;
  upload_types: string[];
};

export type Session =
  | { status: "signed_out"; login_url: string }
  | { status: "forbidden"; email?: string; logout_url: string }
  | { status: "signed_in"; email?: string; logout_url: string; settings: Settings };

export type MetadataChange = {
  caption?: string;
  add_tags?: string[];
  remove_tags?: string[];
};

export type NamedMetadataChange = MetadataChange & { name: string };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(body?.error ?? "The request failed. Please try again.", response.status);
  }
  return body as T;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

const segment = encodeURIComponent;

// Browsers keep thumbnails for a year, so a new shape needs a new URL: change `shape` with THUMBNAIL_SIZE.
export const thumbnailUrl = (image: ImageRecord) =>
  `/app/thumbnails/${segment(image.name)}?v=${image.version}&shape=3x2`;

export const fileUrl = (image: ImageRecord) => `/app/files/${segment(image.name)}?v=${image.version}`;

export const downloadUrl = (image: ImageRecord, fileName: string) =>
  `${fileUrl(image)}&download=${segment(fileName)}`;

export const uploadImage = (name: string, contentBase64: string) =>
  post<ImageRecord>("/api/images", { name, content_base64: contentBase64 });

export function useSession() {
  return useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/session"),
    staleTime: 60_000,
  });
}

/** What the image file says about itself, read only for the image on screen because it opens the file. */
export function useGenerator(image: ImageRecord, enabled: boolean) {
  return useQuery({
    queryKey: ["generator", image.name, image.version],
    queryFn: () => request<{ generator?: string | null }>(`/api/images/${segment(image.name)}/metadata`),
    select: (embedded) => embedded.generator ?? null,
    enabled,
    staleTime: Infinity,
    retry: false,
  });
}

type Collection = "visible" | "hidden";

const collectionOf = (section: Section): Collection => (section === "hidden" ? "hidden" : "visible");

export function useImages(section: Section) {
  const collection = collectionOf(section);
  const client = useQueryClient();
  return useQuery({
    queryKey: ["images", collection],
    queryFn: async () => {
      try {
        const { images } = await request<{ images: ImageRecord[] }>(
          collection === "hidden" ? "/app/hidden" : "/app/images",
        );
        return images;
      } catch (error) {
        // An expired session sends the whole app back to its sign-in page.
        if (error instanceof ApiError && error.status === 401) {
          client.invalidateQueries({ queryKey: ["session"] });
        }
        throw error;
      }
    },
  });
}

/** Every change to one collection: applied on screen first, undone if the server refuses it. */
export function useImageActions(section: Section, batchSize: number) {
  const collection = collectionOf(section);
  const client = useQueryClient();
  const key = ["images", collection];
  const hiddenPrefix = collection === "hidden" ? "/api/hidden/images" : "/api/images";

  const refresh = () => client.invalidateQueries({ queryKey: ["images"] });

  const change = <V, R>(
    run: (variables: V) => Promise<R>,
    apply: (images: ImageRecord[], variables: V) => ImageRecord[],
  ) => ({
    mutationFn: run,
    onMutate: async (variables: V) => {
      await client.cancelQueries({ queryKey: key });
      const previous = client.getQueryData<ImageRecord[]>(key);
      if (previous) client.setQueryData(key, apply(previous, variables));
      return { previous };
    },
    onError: (_error: unknown, _variables: V, context?: { previous?: ImageRecord[] }) => {
      if (context?.previous) client.setQueryData(key, context.previous);
    },
    onSettled: refresh,
  });

  const without = (images: ImageRecord[], name: string) => images.filter((image) => image.name !== name);
  const withoutAll = (images: ImageRecord[], names: string[]) => {
    const leaving = new Set(names);
    return images.filter((image) => !leaving.has(image.name));
  };

  const sameName = (name: string) => name;

  /** Post names a batch at a time to a route that changes all of a batch or none of it. */
  const postNames = (path: string, names: string[], extra?: object) =>
    inBatches(names, batchSize, sameName, async (batch) => {
      await post(path, { names: batch, ...extra });
      return [];
    });

  const deleteNames = (names: string[]) =>
    inBatches(names, batchSize, sameName, async (batch) => {
      const path = collection === "hidden" ? "/api/hidden/delete" : "/api/images/delete";
      return (await post<{ failed: BatchFailure[] }>(path, { names: batch })).failed;
    });

  const setFavorites = useMutation(
    change(
      ({ names, favorite }: { names: string[]; favorite: boolean }) => postNames("/api/favorites", names, { favorite }),
      (images, { names, favorite }) => {
        const changing = new Set(names);
        return images.map((image) => (changing.has(image.name) ? { ...image, favorite } : image));
      },
    ),
  );

  const removeMany = useMutation(change(deleteNames, withoutAll));

  const hideMany = useMutation(change((names: string[]) => postNames("/api/hidden/hide", names), withoutAll));

  const restoreMany = useMutation(change((names: string[]) => postNames("/api/hidden/restore", names), withoutAll));

  const updateMetadataMany = useMutation({
    mutationFn: (changes: NamedMetadataChange[]) =>
      inBatches(
        changes,
        batchSize,
        (item) => item.name,
        async (batch) => (await post<{ failed: BatchFailure[] }>(`${hiddenPrefix}/metadata`, { changes: batch })).failed,
      ),
    onSettled: refresh,
  });

  const setFavorite = useMutation(
    change(
      ({ name, favorite }: { name: string; favorite: boolean }) =>
        post("/api/favorites", { names: [name], favorite }),
      (images, { name, favorite }) =>
        images.map((image) => (image.name === name ? { ...image, favorite } : image)),
    ),
  );

  const remove = useMutation(
    change(
      (name: string): Promise<unknown> =>
        collection === "hidden" ? deleteNames([name]) : request(`/api/images/${segment(name)}`, { method: "DELETE" }),
      without,
    ),
  );

  const hide = useMutation(
    change((name: string) => post("/api/hidden/hide", { names: [name] }), without),
  );

  const restore = useMutation(
    change((name: string) => post("/api/hidden/restore", { names: [name] }), without),
  );

  const rename = useMutation({
    mutationFn: ({ name, newName }: { name: string; newName: string }) =>
      post(`/api/images/${segment(name)}/rename`, { new_name: newName }),
    onSuccess: refresh,
  });

  const updateMetadata = useMutation({
    mutationFn: ({ name, change }: { name: string; change: MetadataChange }) =>
      post<ImageRecord>(`${hiddenPrefix}/${segment(name)}/metadata`, change),
    onSuccess: (saved, { name }) => {
      // Only the edited fields: size, dimensions, and version come from the file.
      client.setQueryData<ImageRecord[]>(key, (images) =>
        images?.map((image) =>
          image.name === name ? { ...image, caption: saved.caption, tags: saved.tags } : image,
        ),
      );
    },
  });

  return {
    setFavorite,
    remove,
    hide,
    restore,
    rename,
    updateMetadata,
    setFavorites,
    removeMany,
    hideMany,
    restoreMany,
    updateMetadataMany,
  };
}

export type ImageActions = ReturnType<typeof useImageActions>;
